from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length

from app.models.category import CATEGORY_ICON_CHOICES
from app.utils.validators import not_blank


class CategoryForm(FlaskForm):
    name = StringField(
        "Nome da categoria",
        validators=[DataRequired(message="Informe o nome."), Length(max=100), not_blank],
    )
    icon = SelectField(
        "Ícone (menu lateral do cardápio)",
        choices=CATEGORY_ICON_CHOICES,
        default="other",
    )
    icon_image = FileField(
        "Ícone próprio (opcional — substitui o ícone da biblioteca)",
        validators=[FileAllowed(["png", "jpg", "jpeg", "webp", "heic", "heif"], message="Formato não suportado.")],
    )
    is_active = BooleanField("Categoria ativa (visível no cardápio)", default=True)
    submit = SubmitField("Salvar categoria")
