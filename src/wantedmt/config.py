"""Source registry and thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CKAN_BASE = "https://data.gov.ua/api/3/action"

PACKAGE_SHOW = f"{CKAN_BASE}/package_show"

RESOURCE_SHOW = f"{CKAN_BASE}/resource_show"


@dataclass(frozen=True)
class Source:
    """One publication of the register. The register moved from the Ministry of Internal …"""

    key: str
    dataset_id: str
    resource_id: str
    publisher: str
    uppercase_fields: bool
    frozen: bool


SOURCES: dict[str, Source] = {
    "mvs": Source(
        key="mvs",
        dataset_id="5c6c156f-21ee-42cd-8da3-dcde6828be97",
        resource_id="b8476483-9cf7-4e97-8c31-f757fa7dd825",
        publisher="Міністерство внутрішніх справ України",
        uppercase_fields=True,
        frozen=True,
    ),
    "npu": Source(
        key="npu",
        dataset_id="30b67898-1968-4d99-8058-298b56f22bff",
        resource_id="2059a788-fa1e-4003-b0f4-1a8c342b3b48",
        publisher="Національна поліція України",
        uppercase_fields=False,
        frozen=False,
    ),
}


FIELDS = ("id", "ovd", "insert_date", "nz", "imei", "nk", "dk", "dtl")


FIELD_TITLES: dict[str, str] = {
    "id": "Унікальний ідентифікатор запису",
    "ovd": "Назва підрозділу, що зареєстрував інформацію",
    "insert_date": "Дата внесення інформації",
    "nz": "Марка/модель",
    "imei": "ІМЕІ/номер",
    "nk": "Номер реєстрації в журналі єдиного обліку підрозділу, що зареєстрував інформацію",
    "dk": "Дата реєстрації в журналі єдиного обліку",
    "dtl": "деталі",
}


MIN_SNAPSHOT_ROWS = 700_000


MAX_DISAPPEARED_SHARE_PER_DAY = 0.02

MAX_DISAPPEARED_SHARE = 0.25


MIN_REVISION_SIZE_SHARE = 0.5


MIN_REVISION_BYTES = 50_000_000


PERSONAL_FIELDS = frozenset({"nomer", "dtl.nomer"})


DTL_PHONE_PATTERN = "^[0-9]{9,13}$"


DEFAULT_DB = Path("data/wantedmt.duckdb")

DEFAULT_WORK_DIR = Path("data/work")
