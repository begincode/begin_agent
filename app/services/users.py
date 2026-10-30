"""Platform account directory, independent of the selected tenant."""

from sqlalchemy import select

from app.models.platform import PlatformUser
from app.repositories.platform import public


def user_view(row):
    return {**public(row), "account_kind": "PASSWORD" if row.username else "LEGACY"}


async def user_page(session, search="", status=None, platform_admin=None, limit=50, offset=0):
    query = select(PlatformUser)
    if search:
        query = query.where(
            PlatformUser.username.icontains(search, autoescape=True)
            | PlatformUser.display_name.icontains(search, autoescape=True)
            | PlatformUser.user_code.icontains(search, autoescape=True)
            | PlatformUser.external_user_id.icontains(search, autoescape=True)
        )
    if status is not None:
        query = query.where(PlatformUser.status == status)
    if platform_admin is not None:
        query = query.where(PlatformUser.platform_admin.is_(platform_admin))
    rows = await session.scalars(
        query.order_by(PlatformUser.created_at.desc(), PlatformUser.user_code.desc())
        .limit(limit)
        .offset(offset)
    )
    return [user_view(row) for row in rows]
