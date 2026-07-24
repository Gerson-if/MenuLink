"""
Regras de negócio da criação de um pedido a partir do carrinho do
cardápio público.

Princípios de segurança/integridade aplicados aqui:
- O preço de cada item vem SEMPRE do produto no banco (nunca do payload
  do cliente) — impossível manipular o valor total pelo navegador.
- Todo produto do carrinho é validado como pertencente ao tenant correto
  e ativo — um product_id de outra loja, ou de um produto desativado,
  é rejeitado.
- Regras do lojista são aplicadas: entrega desabilitada, retirada
  desabilitada, e pedido mínimo (quando configurado).
- Snapshot: nome e preço do produto são copiados para OrderItem no
  momento da compra — se o lojista mudar o preço depois, pedidos
  antigos não mudam de valor retroativamente.
"""

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import DeliveryType, Order, OrderItem, OrderItemChoice, OrderStatus, Product
from app.repositories.customer_repository import CustomerRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.utils.phone import normalize_br_phone

# Transições de status válidas para o pedido, geridas pelo lojista.
# Chave = status atual, valor = conjunto de status para os quais é
# permitido avançar a partir dali. `completed` e `canceled` são estados
# terminais (não aparecem como chave = nenhuma transição posterior).
VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING.value: {
        OrderStatus.CONFIRMED.value,
        OrderStatus.CANCELED.value,
    },
    OrderStatus.CONFIRMED.value: {
        OrderStatus.PREPARING.value,
        OrderStatus.CANCELED.value,
    },
    OrderStatus.PREPARING.value: {
        OrderStatus.OUT_FOR_DELIVERY.value,
        OrderStatus.READY_FOR_PICKUP.value,
        OrderStatus.CANCELED.value,
    },
    OrderStatus.OUT_FOR_DELIVERY.value: {
        OrderStatus.COMPLETED.value,
        OrderStatus.CANCELED.value,
    },
    OrderStatus.READY_FOR_PICKUP.value: {
        OrderStatus.COMPLETED.value,
        OrderStatus.CANCELED.value,
    },
}

# Permite "voltar" um passo (ex: marcou "em preparo" sem querer, volta pra
# "confirmado"). Não é simplesmente o inverso do mapa acima — cancelado
# nunca aparece aqui (cancelamento não se desfaz) e cada status só volta
# para o único passo anterior que faz sentido no fluxo.
PREVIOUS_STATUS: dict[str, str] = {
    OrderStatus.CONFIRMED.value: OrderStatus.PENDING.value,
    OrderStatus.PREPARING.value: OrderStatus.CONFIRMED.value,
    OrderStatus.OUT_FOR_DELIVERY.value: OrderStatus.PREPARING.value,
    OrderStatus.READY_FOR_PICKUP.value: OrderStatus.PREPARING.value,
}


class OrderValidationError(Exception):
    """Erro de regra de negócio, com mensagem segura para exibir ao cliente final."""


class OrderService:
    MAX_ORDER_NUMBER_RETRIES = 3

    def __init__(self, tenant):
        self.tenant = tenant
        self.order_repo = OrderRepository(tenant.id)
        self.product_repo = ProductRepository(tenant.id)
        self.customer_repo = CustomerRepository(tenant.id)

    def create_order(self, payload: dict) -> Order:
        # Normalizado uma única vez aqui — CheckoutSchema já garantiu que é
        # um telefone brasileiro válido, então normalize_br_phone nunca
        # retorna None nesse ponto. _resolve_customer e _persist_order
        # abaixo leem do mesmo payload, já normalizado.
        payload["customer_phone"] = normalize_br_phone(payload["customer_phone"])

        delivery_type = payload["delivery_type"]

        self._validate_delivery_rules(delivery_type)
        items_data, subtotal_cents = self._resolve_items(payload["items"])

        delivery_fee_cents = 0
        if delivery_type == DeliveryType.DELIVERY.value:
            delivery_fee_cents = self.tenant.delivery_fee_cents or 0
            if self.tenant.free_delivery_above_cents and subtotal_cents >= self.tenant.free_delivery_above_cents:
                delivery_fee_cents = 0

        total_cents = subtotal_cents + delivery_fee_cents

        if self.tenant.min_order_cents and subtotal_cents < self.tenant.min_order_cents:
            raise OrderValidationError(
                f"Pedido mínimo de R$ {self.tenant.min_order_cents / 100:.2f}. "
                f"Seu carrinho está em R$ {subtotal_cents / 100:.2f}."
            )

        customer = self._resolve_customer(payload["customer_name"], payload["customer_phone"])

        order = self._persist_order(
            payload=payload,
            customer_id=customer.id,
            items_data=items_data,
            subtotal_cents=subtotal_cents,
            delivery_fee_cents=delivery_fee_cents,
            total_cents=total_cents,
        )
        return order

    # --- Regras ---
    def _validate_delivery_rules(self, delivery_type: str) -> None:
        if delivery_type == DeliveryType.DELIVERY.value and not self.tenant.delivery_enabled:
            raise OrderValidationError("Esta loja não está aceitando pedidos com entrega no momento.")
        if delivery_type == DeliveryType.PICKUP.value and not self.tenant.pickup_enabled:
            raise OrderValidationError("Esta loja não está aceitando retirada no local no momento.")

    def _resolve_items(self, raw_items: list[dict]):
        items_data = []
        subtotal_cents = 0

        for raw_item in raw_items:
            product = self.product_repo.get_by_id(raw_item["product_id"])
            if product is None or not product.is_active:
                raise OrderValidationError(
                    "Um dos itens do carrinho não está mais disponível. Atualize a página e tente novamente."
                )

            quantity = raw_item["quantity"]
            option_ids = set(raw_item.get("option_ids") or [])

            selected_options, choices_data = self._resolve_options(product, option_ids)

            unit_price_cents = product.price_cents + sum(o.extra_price_cents for o in selected_options)
            item_subtotal = unit_price_cents * quantity
            subtotal_cents += item_subtotal

            items_data.append(
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "unit_price_cents": unit_price_cents,
                    "quantity": quantity,
                    "subtotal_cents": item_subtotal,
                    "choices": choices_data,
                }
            )

        return items_data, subtotal_cents

    def _resolve_options(self, product, option_ids: set[int]):
        """
        Valida e resolve as opções de variação/complemento escolhidas
        para um produto.

        Regras aplicadas (todas no servidor, nunca confiando no que o
        cliente diz ter escolhido):
        - Toda opção enviada precisa pertencer a um grupo DESSE produto
          e DESSE tenant — impede injetar uma opção de outro produto ou
          de outra loja para tentar burlar preço/composição.
        - Grupo obrigatório (`is_required`) sem nenhuma opção
          selecionada é rejeitado.
        - Grupo de escolha única (`single_choice`, o que inclui toda
          variação) com mais de uma opção selecionada é rejeitado.
        """
        options_by_id = {}
        for group in product.complement_groups:
            for option in group.options:
                if option.is_active:
                    options_by_id[option.id] = (group, option)

        selected: list = []
        choices_data: list[dict] = []
        selections_by_group: dict[int, list] = {}

        for option_id in option_ids:
            match = options_by_id.get(option_id)
            if match is None:
                raise OrderValidationError(
                    "Uma das opções escolhidas não está mais disponível. Atualize a página e tente novamente."
                )
            group, option = match
            selected.append(option)
            selections_by_group.setdefault(group.id, []).append((group, option))

        for group in product.complement_groups:
            group_selections = selections_by_group.get(group.id, [])

            if group.is_required and not group_selections:
                raise OrderValidationError(
                    f"Escolha uma opção para '{group.name}' antes de continuar."
                )
            if group.single_choice and len(group_selections) > 1:
                raise OrderValidationError(
                    f"Só é possível escolher uma opção para '{group.name}'."
                )

            for group_obj, option in group_selections:
                choices_data.append(
                    {
                        "group_name": group_obj.name,
                        "option_name": option.name,
                        "extra_price_cents": option.extra_price_cents,
                    }
                )

        return selected, choices_data

    def _resolve_customer(self, name: str, phone: str):
        customer = self.customer_repo.get_by_phone(phone)
        if customer is not None:
            if customer.name != name:
                customer.name = name  # mantém o nome mais recente informado
            return customer

        from app.models import Customer

        customer = Customer(tenant_id=self.tenant.id, name=name, phone=phone)
        db.session.add(customer)
        try:
            db.session.flush()
        except IntegrityError:
            # Corrida: dois checkouts concorrentes com o mesmo telefone
            # (duplo clique, duas abas, retry de rede) — a constraint
            # uq_customer_tenant_phone barrou nossa tentativa porque outra
            # requisição já criou o cliente entre nossa consulta e o
            # insert. Descarta essa tentativa e usa o registro que "ganhou
            # a corrida", em vez de duplicar o cadastro ou estourar 500.
            db.session.rollback()
            customer = self.customer_repo.get_by_phone(phone)
            if customer is None:
                raise  # não deveria acontecer — não mascara um erro real
            if customer.name != name:
                customer.name = name
        return customer

    def _persist_order(self, *, payload, customer_id, items_data, subtotal_cents, delivery_fee_cents, total_cents) -> Order:
        last_error = None

        for _ in range(self.MAX_ORDER_NUMBER_RETRIES):
            order_number = self.order_repo.next_order_number()

            order = Order(
                tenant_id=self.tenant.id,
                order_number=order_number,
                customer_id=customer_id,
                customer_name=payload["customer_name"],
                customer_phone=payload["customer_phone"],
                delivery_type=payload["delivery_type"],
                address_street=payload.get("address_street"),
                address_number=payload.get("address_number"),
                address_complement=payload.get("address_complement"),
                address_neighborhood=payload.get("address_neighborhood"),
                address_city=payload.get("address_city"),
                address_reference=payload.get("address_reference"),
                payment_method=payload["payment_method"],
                subtotal_cents=subtotal_cents,
                delivery_fee_cents=delivery_fee_cents,
                total_cents=total_cents,
                notes=payload.get("notes"),
            )
            order.items = [
                OrderItem(
                    tenant_id=self.tenant.id,
                    product_id=item_data["product_id"],
                    product_name=item_data["product_name"],
                    unit_price_cents=item_data["unit_price_cents"],
                    quantity=item_data["quantity"],
                    subtotal_cents=item_data["subtotal_cents"],
                    choices=[
                        OrderItemChoice(tenant_id=self.tenant.id, **choice_data)
                        for choice_data in item_data.get("choices", [])
                    ],
                )
                for item_data in items_data
            ]

            db.session.add(order)
            try:
                db.session.commit()
                return order
            except IntegrityError as exc:
                db.session.rollback()
                last_error = exc
                continue  # provável colisão de order_number sob concorrência — tenta de novo

        raise OrderValidationError("Não foi possível registrar o pedido. Tente novamente.") from last_error

    # --- Consulta e gestão de pedidos (painel do lojista) ---
    def get_or_404(self, order_id: int) -> Order:
        from flask import abort

        order = self.order_repo.get_by_id(order_id)
        if order is None:
            abort(404)
        return order

    def get_locked_or_404(self, order_id: int) -> Order:
        """
        Como get_or_404, mas com lock de linha (SELECT ... FOR UPDATE) —
        use nas ações que MUDAM o status do pedido (aceitar, rejeitar,
        atualizar status, voltar status), nunca em telas só de leitura.

        Sem isso, duas requisições concorrentes no mesmo pedido (dois
        atendentes, duas abas do lojista clicando quase ao mesmo tempo)
        liam o mesmo status "pending", cada uma validava sua própria
        transição contra esse status já desatualizado, e a segunda a
        commitar vencia silenciosamente — sem revalidar o status atual,
        podia aplicar uma transição que não fazia mais sentido (ex.:
        confirmar um pedido que a outra aba acabou de cancelar). O lock
        serializa a segunda requisição até a primeira terminar, e ela
        então lê o status já atualizado antes de decidir.

        (Em SQLite — usado nos testes — FOR UPDATE é ignorado pelo
        dialect, sem efeito nem erro; o lock de fato só existe em
        Postgres, usado em produção.)
        """
        from flask import abort
        from sqlalchemy import select

        stmt = (
            select(Order)
            .where(Order.tenant_id == self.tenant.id, Order.id == order_id)
            .with_for_update()
        )
        order = db.session.execute(stmt).scalar_one_or_none()
        if order is None:
            abort(404)
        return order

    def available_transitions(self, order: Order) -> list[str]:
        return sorted(VALID_STATUS_TRANSITIONS.get(order.status.value, set()))

    def can_revert(self, order: Order) -> bool:
        return order.status.value in PREVIOUS_STATUS

    def revert_status(self, order: Order) -> Order:
        previous = PREVIOUS_STATUS.get(order.status.value)
        if previous is None:
            raise OrderValidationError(f"O pedido em '{order.status.value}' não pode voltar de status.")
        order.status = previous
        db.session.commit()
        return order

    def update_status(self, order: Order, new_status: str) -> Order:
        current = order.status.value
        allowed = VALID_STATUS_TRANSITIONS.get(current, set())

        if new_status not in allowed:
            raise OrderValidationError(
                f"Não é possível mudar o pedido de '{current}' para '{new_status}'."
            )

        order.status = new_status
        db.session.commit()
        return order
