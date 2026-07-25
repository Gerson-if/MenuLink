from app.extensions import db
from app.models import Banner
from app.repositories.banner_repository import BannerRepository
from app.utils.uploads import save_banner_image, delete_product_image_file, InvalidImageError


class BannerService:
    def __init__(self, tenant):
        self.tenant = tenant
        self.repo = BannerRepository(tenant.id)

    def list_all(self):
        return self.repo.list_ordered()

    def get_or_404(self, banner_id: int) -> Banner:
        from flask import abort

        banner = self.repo.get_by_id(banner_id)
        if banner is None:
            abort(404)
        return banner

    def create(self, *, title: str, subtitle: str, link_url: str, file_storage, is_active: bool) -> Banner:
        try:
            relative_path, dominant_color = save_banner_image(self.tenant.id, file_storage)
        except InvalidImageError:
            raise

        max_order = max([b.display_order for b in self.repo.list_ordered()], default=-1)

        banner = Banner(
            tenant_id=self.tenant.id,
            title=title.strip(),
            subtitle=(subtitle or "").strip() or None,
            link_url=(link_url or "").strip() or None,
            image_path=relative_path,
            dominant_color=dominant_color,
            display_order=max_order + 1,
            is_active=is_active,
        )
        db.session.add(banner)
        db.session.commit()
        return banner

    def update(self, banner: Banner, *, title: str, subtitle: str, link_url: str, is_active: bool, file_storage=None) -> Banner:
        banner.title = title.strip()
        banner.subtitle = (subtitle or "").strip() or None
        banner.link_url = (link_url or "").strip() or None
        banner.is_active = is_active

        if file_storage and file_storage.filename:
            old_path = banner.image_path
            banner.image_path, banner.dominant_color = save_banner_image(self.tenant.id, file_storage)
            delete_product_image_file(old_path)

        db.session.commit()
        return banner

    def toggle_active(self, banner: Banner) -> Banner:
        banner.is_active = not banner.is_active
        db.session.commit()
        return banner

    def delete(self, banner: Banner) -> None:
        delete_product_image_file(banner.image_path)
        db.session.delete(banner)
        db.session.commit()
