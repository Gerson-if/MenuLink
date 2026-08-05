from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, URL

from app.utils.validators import not_blank


class BannerForm(FlaskForm):
    title = StringField("Título", validators=[DataRequired(), Length(max=120), not_blank])
    subtitle = StringField("Subtítulo (opcional)", validators=[Optional(), Length(max=200)])
    link_url = StringField("Link ao clicar (opcional)", validators=[Optional(), URL(require_tld=False, message="URL inválida."), Length(max=255)])
    image = FileField(
        "Imagem do banner",
        validators=[FileAllowed(["png", "jpg", "jpeg", "webp", "heic", "heif"], message="Formato não suportado.")],
    )
    is_active = BooleanField("Banner ativo (visível no cardápio)", default=True)
    submit = SubmitField("Salvar banner")
