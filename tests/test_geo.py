"""Region extraction, including the three ways it silently returned nothing."""

from __future__ import annotations

import pytest

from wantedmt.normalize.geo import REGIONS, region_of, unit_type_of


class TestRegion:
    @pytest.mark.parametrize(
        ("ovd", "region"),
        [
            (
                "ЧЕРНІГІВСЬКЕ РАЙОННЕ УПРАВЛІННЯ ПОЛІЦІЇ ГУНП В ЧЕРНІГІВСЬКІЙ ОБЛАСТІ",
                "Чернігівська область",
            ),
            (
                "МЕЛІТОПОЛЬСЬКЕ РАЙОННЕ УПРАВЛІННЯ ПОЛІЦІЇ ГУНП В ЗАПОРІЗЬКІЙ ОБЛАСТІ",
                "Запорізька область",
            ),
            ("ЛУЦЬКЕ РАЙОННЕ УПРАВЛІННЯ ПОЛІЦІЇ ГУНП У ВОЛИНСЬКІЙ ОБЛАСТІ", "Волинська область"),
            (
                "ІВАНО-ФРАНКІВСЬКЕ РАЙОННЕ УПРАВЛІННЯ  ПОЛІЦІЇ  ГУНП В ІВАНО-ФРАНКІВСЬКІЙ ОБЛАСТІ",
                "Івано-Франківська область",
            ),
        ],
    )
    def test_current_naming(self, ovd, region):
        assert region_of(ovd) == region

    @pytest.mark.parametrize(
        ("ovd", "region"),
        [
            ("ЖОВТНЕВИЙ РВ ЛУГАНСЬКОГО МУ ГУМВСУ У ЛУГАНСЬКІЙ ОБЛ.", "Луганська область"),
            ("СУВОРОВСЬКИЙ РВ МУ УМВСУ В ХЕРСОНСЬКІЙ ОБЛ.", "Херсонська область"),
            ("МЕЛІТОПОЛЬСЬКИЙ РОВД ЗАПОРІЗЬКОЇ ОБЛ.", "Запорізька область"),
        ],
    )
    def test_legacy_naming(self, ovd, region):
        assert region_of(ovd) == region

    @pytest.mark.parametrize(
        ("ovd", "region"),
        [
            ("ШЕВЧЕНКІВСЬКЕ УПРАВЛІННЯ ПОЛІЦІЇ ГУНП В М. КИЄВІ", "м. Київ"),
            ("ЛЕНІНСЬКИЙ РВ СЕВАСТОПОЛЬСЬКОГО МУ", "м. Севастополь"),
            ("БАЛАКЛАВСЬКИЙ РВ СЕВАСТПОЛЬСЬКОГО МУ", "м. Севастополь"),
            (
                "КИЇВСЬКИЙ РВ СІМФЕРОПОЛЬСЬКОГО МВ ГУМВС УКРАЇНИ В АР КРИМ",
                "Автономна Республіка Крим",
            ),
            ("ОМ 1 Г.ЯЛТЫ АР КРЫМ", "Автономна Республіка Крим"),
        ],
    )
    def test_cities_with_special_status_and_crimea(self, ovd, region):
        assert region_of(ovd) == region

    @pytest.mark.parametrize(
        "ovd",
        [
            "ЛВ НА СТАНЦІЇ ОСНОВА УМВСУ НА ПІВДЕННІЙ ЗАЛІЗНИЦІ",
            "ЛВ НА СТ. КИЇВ-ПАСАЖИРСЬКИЙ УМВСУ НА ПІВД.-ЗАХ. ЗАЛІЗНИЦІ",
        ],
    )
    def test_railway_units_belong_to_no_oblast(self, ovd):
        """Empty is the correct answer here — a railway line has no region."""
        assert region_of(ovd) is None

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_input(self, value):
        assert region_of(value) is None

    def test_every_region_name_resolves_to_itself(self):
        """Guards the stem table against a typo in the list itself."""

        for region in REGIONS:
            assert region_of(f"ВІДДІЛ ПОЛІЦІЇ ГУНП В {region.upper()} ОБЛАСТІ") == (
                f"{region} область"
            )


class TestUnitType:
    @pytest.mark.parametrize(
        ("ovd", "expected"),
        [
            ("ЧЕРНІГІВСЬКЕ РАЙОННЕ УПРАВЛІННЯ ПОЛІЦІЇ ГУНП", "районне управління"),
            ("ШЕВЧЕНКІВСЬКЕ УПРАВЛІННЯ ПОЛІЦІЇ ГУНП", "управління"),
            ("ВІДДІЛ ПОЛІЦІЇ №2", "відділ"),
        ],
    )
    def test_unit_type(self, ovd, expected):
        assert unit_type_of(ovd) == expected
