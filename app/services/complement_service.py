from app.extensions import db
from app.models import ComplementGroup, ComplementOption


class ComplementService:
    def __init__(self, tenant):
        self.tenant = tenant

    # --- Grupos ---
    def create_group(
        self, product, *, name: str, is_variation: bool, is_required: bool, single_choice: bool,
        allow_quantity: bool = False,
    ) -> ComplementGroup:
        single_choice = single_choice or is_variation
        max_order = max([g.display_order for g in product.complement_groups], default=-1)
        group = ComplementGroup(
            tenant_id=self.tenant.id,
            product_id=product.id,
            name=name.strip(),
            is_variation=is_variation,
            is_required=is_required,
            single_choice=single_choice,
            # Só faz sentido escolher quantidade num grupo de múltipla
            # escolha que não seja variação (ex: Tamanho não "some" —
            # o próprio ComplementGroupForm já esconde isso via JS, mas
            # o servidor é quem garante de verdade).
            allow_quantity=allow_quantity and not is_variation and not single_choice,
            display_order=max_order + 1,
        )
        db.session.add(group)
        db.session.commit()
        return group

    def get_group_or_404(self, group_id: int) -> ComplementGroup:
        from flask import abort

        group = ComplementGroup.query.filter_by(id=group_id, tenant_id=self.tenant.id).first()
        if group is None:
            abort(404)
        return group

    def delete_group(self, group: ComplementGroup) -> None:
        db.session.delete(group)
        db.session.commit()

    # --- Opções ---
    def add_option(self, group: ComplementGroup, *, name: str, extra_price_reais: float) -> ComplementOption:
        max_order = max([o.display_order for o in group.options], default=-1)
        option = ComplementOption(
            tenant_id=self.tenant.id,
            group_id=group.id,
            name=name.strip(),
            extra_price_cents=round((extra_price_reais or 0) * 100),
            display_order=max_order + 1,
        )
        db.session.add(option)
        db.session.commit()
        return option

    def get_option_or_404(self, option_id: int) -> ComplementOption:
        from flask import abort

        option = ComplementOption.query.filter_by(id=option_id, tenant_id=self.tenant.id).first()
        if option is None:
            abort(404)
        return option

    def delete_option(self, option: ComplementOption) -> None:
        db.session.delete(option)
        db.session.commit()
