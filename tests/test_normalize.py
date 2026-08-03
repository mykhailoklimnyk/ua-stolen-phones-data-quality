"""Rules that broke at least once, pinned so they cannot break silently again."""

from __future__ import annotations

import re

import pytest

from wantedmt.normalize.imei import canonical, luhn_check_digit, luhn_valid
from wantedmt.normalize.text import defuse_homoglyphs, nearest, normalize_model, resolve

CYRILLIC = re.compile(r"[Ѐ-ӿ]")


class TestHomoglyphs:
    """Substitution must run in one direction only. Latin words carrying a stray Cyrillic …"""

    @pytest.mark.parametrize(
        ("spelling", "brand"),
        [
            ("XІAOMІ", "Xiaomi"),
            ("APPLE ІPHONE", "Apple"),
            ("HUAWEІ", "Huawei"),
            ("MEІZU", "Meizu"),
        ],
    )
    def test_latin_word_with_smuggled_cyrillic_is_repaired(self, spelling, brand):
        assert resolve(spelling)["brand"] == brand

    @pytest.mark.parametrize(
        ("spelling", "brand"),
        [
            ("САМСУНГ", "Samsung"),
            ("НОКИА", "Nokia"),
            ("СОНИЭРИКСОН", "Sony Ericsson"),
            ("НТС", "HTC"),
        ],
    )
    def test_transliterations_survive_untouched(self, spelling, brand):
        assert resolve(spelling)["brand"] == brand

    @pytest.mark.parametrize("spelling", ["НОКИА6230I", "СОНИЭРИКСОНК750I"])
    def test_cyrillic_word_with_one_latin_letter_is_not_mangled(self, spelling):
        """Mixed script is not enough — which alphabet dominates decides."""
        cleaned, changed = defuse_homoglyphs(spelling)
        assert not changed, f"{spelling} was rewritten to {cleaned}"
        assert resolve(spelling)["brand"] is not None

    def test_multiword_cyrillic_brand_keeps_its_word(self):
        """САМСУНГ GALAXY S8 must not become CAMCУHГ GALAXY S8."""
        result = resolve("САМСУНГ GALAXY S8")
        assert result["brand"] == "Samsung"
        assert result["model"] == "GALAXY S8"


class TestModelCodes:
    """Inside a model code a Cyrillic letter is a keyboard artefact, not a word."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("А50", "A50"), ("Е250", "E250"), ("К750I", "K750I"), ("Х2", "X2")],
    )
    def test_alphanumeric_codes_are_latinised(self, raw, expected):
        assert normalize_model(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("Д600", "D600"), ("Д900", "D900"), ("Д520", "D520"), ("Г900", "G900")],
    )
    def test_cyrillic_without_a_latin_twin_is_transliterated(self, raw, expected):
        """Д has no look-alike, so shape-matching alone leaves Samsung D600 broken."""
        assert normalize_model(raw) == expected

    def test_shape_wins_over_sound_for_ambiguous_letters(self):
        """В reads as B by shape and V by sound, and shape is the better bet. The field is …"""
        assert normalize_model("В500") == "B500"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("ЕРИКСОН", "ERICSSON"), ("ГЕЛЕКСІ", "GALAXY"), ("РЕДМИ", "REDMI"), ("ДУОС", "DUOS")],
    )
    def test_transliterated_line_words_need_the_dictionary(self, raw, expected):
        """No character rule turns ГЕЛЕКСІ into GALAXY — it is a different word."""
        assert normalize_model(raw) == expected

    @pytest.mark.parametrize(("raw", "expected"), [("Х", "X"), ("Х 2", "X 2")])
    def test_single_letter_codes_are_latinised(self, raw, expected):
        assert normalize_model(raw) == expected

    def test_words_without_digits_are_left_alone(self):
        assert normalize_model("GALAXY S8") == "GALAXY S8"

    @pytest.mark.parametrize(
        "spelling",
        ["САМСУНГ А50", "SAMSUNG Е250", "СОНИЭРИКСОН К750I", "NOKIA Х2"],
    )
    def test_published_model_carries_no_cyrillic(self, spelling):
        model = resolve(spelling)["model"]
        assert model and not CYRILLIC.search(model), f"{spelling} -> {model}"

    @pytest.mark.parametrize(
        "spelling",
        ["SAMSUNG", "САМСУНГ", "XІAOMІ REDMI 9C", "НОКИА6233", "APPLE ІPHONE 12"],
    )
    def test_published_brand_carries_no_cyrillic(self, spelling):
        brand = resolve(spelling)["brand"]
        assert brand and not CYRILLIC.search(brand), f"{spelling} -> {brand}"


class TestResolution:
    def test_brand_and_manufacturer_are_separate_axes(self):
        """Redmi is a brand of its own; folding it into Xiaomi loses both counts."""
        result = resolve("REDMI NOTE 8")
        assert result["brand"] == "Redmi"
        assert result["manufacturer"] == "Xiaomi"
        assert result["model"] == "NOTE 8"

    @pytest.mark.parametrize(
        ("spelling", "brand", "model"),
        [
            ("NOKIA6300", "Nokia", "6300"),
            ("LGKP105", "LG", "KP105"),
            ("IPHONE4", "Apple", "4"),
        ],
    )
    def test_brand_glued_to_model_is_split(self, spelling, brand, model):
        result = resolve(spelling)
        assert (result["brand"], result["model"], result["method"]) == (brand, model, "glued")

    @pytest.mark.parametrize(
        ("typo", "brand"),
        [
            ("HOKIA", "Nokia"),
            ("NOKAI", "Nokia"),
            ("SAMSYNG", "Samsung"),
            ("SONI", "Sony"),
            ("SIMENS", "Siemens"),
        ],
    )
    def test_typos_resolve(self, typo, brand):
        assert resolve(typo)["brand"] == brand

    @pytest.mark.parametrize("word", ["ТЕЛЕФОН", "МОБИЛЬН ТЕЛЕФОН"])
    def test_generic_words_are_refused(self, word):
        """A resolver that cannot decline publishes guesses as facts."""
        assert resolve(word)["brand"] is None

    def test_empty_input_is_not_an_error(self):
        assert resolve("")["method"] == "empty"
        assert resolve(None)["brand"] is None

    def test_nearest_ranks_the_closest_alias_first(self):
        assert nearest("SAMSYNG", limit=1)[0][1] == "Samsung"


class TestImei:
    def test_check_digit_matches_the_standard_example(self):
        assert luhn_check_digit("49015420323751") == 8
        assert luhn_valid("490154203237518")

    def test_missing_check_digit_is_restored(self):
        assert canonical("35629301123456") == "356293011234565"

    def test_a_wrong_check_digit_is_never_repaired(self):
        """Rewriting a digit to satisfy the checksum would invent data."""
        assert canonical("356293011234567") == "356293011234567"
        assert not luhn_valid("356293011234567")

    def test_separators_are_stripped(self):
        assert canonical("35-629301-123456-7") == "356293011234567"

    def test_software_version_suffix_is_dropped(self):
        assert canonical("356293011234567" + "99") == "356293011234567"

    @pytest.mark.parametrize("junk", ["000000000000000", "111111111111111", "123", "", None])
    def test_placeholders_and_fragments_yield_nothing(self, junk):
        assert canonical(junk) is None

    def test_tac_is_the_leading_eight_digits(self):
        imei = canonical("35629301123456")
        assert imei is not None and imei[:8] == "35629301"
