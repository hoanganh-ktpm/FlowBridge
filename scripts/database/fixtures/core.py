"""Optional draft registry fixtures; no users, grants or fake engine success."""

from datetime import datetime

from app.core.db.models import CORE_TABLE_PRIORITIES
from scripts.database.config import database_url, layout
from scripts.database.fixtures.business import VIETNAM, demo_id


def core_seed_rows():
    stamp = datetime(2026, 10, 20, 15, tzinfo=VIETNAM)
    rows = {name: [] for name in CORE_TABLE_PRIORITIES}
    for domain, name in (
        ("sales", "Phòng kinh doanh"),
        ("warehouse", "Phòng kho"),
        ("logistics", "Phòng vận chuyển"),
    ):
        common = {
            "created_at": stamp,
            "updated_at": stamp,
            "created_by": None,
            "updated_by": None,
            "revision": 1,
        }
        department_id = demo_id(f"department:{domain}")
        application_id = demo_id(f"application:{domain}")
        rows["departments"].append(
            {
                **common,
                "id": department_id,
                "code": domain,
                "name": name,
                "description": "Đơn vị mẫu; không tự cấp quyền quản trị.",
                "keycloak_group_ref": None,
                "status": "ACTIVE",
            }
        )
        rows["applications"].append(
            {
                **common,
                "id": application_id,
                "department_id": department_id,
                "app_key": domain,
                "name": f"Ứng dụng {domain}",
                "description": "Cấu hình mẫu, chưa kết nối runtime.",
                "business_api_base_url": None,
                "status": "DRAFT",
            }
        )
        rows["data_sources"].append(
            {
                **common,
                "id": demo_id(f"source:{domain}"),
                "application_id": application_id,
                "source_key": f"{domain}.postgres",
                "db_type": "POSTGRESQL",
                "host": f"{domain}.invalid",
                "port": 5432,
                "database_name": (
                    database_url(domain).database if layout() == "schemas" else f"{domain}_db"
                ),
                "db_user": "configure_reader",
                "secret_ref": f"secrets/{domain}/read-only",
                "ssl_mode": "verify-full",
                "hasura_source_name": f"{domain}_postgres",
                "status": "DRAFT",
                "last_checked_at": None,
            }
        )
    return rows
