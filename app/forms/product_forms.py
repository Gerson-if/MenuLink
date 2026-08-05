from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, DecimalField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.utils.validators import not_blank


class ProductForm(FlaskForm):
    name = StringField(
        "Nome do produto",
        validators=[DataRequired(message="Informe o nome."), Length(max=150), not_blank],
    )
    category_id = SelectField("Categoria", coerce=int, validators=[Optional()])
    description = TextAreaField("Descrição", validators=[Optional(), Length(max=2000)])
    price = DecimalField(
        "Preço (R$)",
        places=2,
        validators=[DataRequired(message="Informe o preço."), NumberRange(min=0, message="Preço não pode ser negativo.")],
    )
    cost_price = DecimalField(
        "Preço de custo (R$) — opcional",
        places=2,
        validators=[Optional(), NumberRange(min=0, message="Custo não pode ser negativo.")],
    )
    tag = StringField(
        "Selo do produto (opcional)",
        validators=[Optional(), Length(max=40)],
    )
    is_active = BooleanField("Produto ativo (visível no cardápio)", default=True)
    submit = SubmitField("Salvar produto")

    def validate_cost_price(self, field):
        # Custo maior que o preço de venda quase sempre é erro de digitação
        # (ex: trocou os dois campos) — avisamos em vez de deixar passar
        # silenciosamente uma margem negativa.
        if field.data is not None and self.price.data is not None and field.data > self.price.data:
            raise ValidationError("O preço de custo não pode ser maior que o preço de venda.")


class ImageUploadForm(FlaskForm):
    image = FileField(
        "Imagem do produto",
        validators=[
            FileRequired(message="Selecione uma imagem."),
            FileAllowed(["png", "jpg", "jpeg", "webp", "heic", "heif"], message="Formato não suportado (use PNG, JPG, JPEG ou WEBP)."),
        ],
    )
    submit = SubmitField("Enviar imagem")
