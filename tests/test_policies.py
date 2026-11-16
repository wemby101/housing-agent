from pathlib import Path

from housing.policies import Profile, load_policies, match_policies

sample = load_policies(str(Path(__file__).parent / "fixtures" / "policies_sample.json"))
JEONSE = dict(age=26, income_won=32_000_000, married=False, homeless=True, deposit_won=220_000_000, monthly_rent_won=0)


def ids(matches):
    return {m.policy["id"] for m in matches}


def test_guarantee_fee_support_eligible():
    m = {x.policy["id"]: x for x in match_policies(Profile(**JEONSE), sample)}
    assert set(m) >= {"guarantee_fee"} and m["guarantee_fee"].status == "eligible"


def test_income_over_limit_excluded():
    assert "guarantee_fee" not in ids(match_policies(Profile(**{**JEONSE, "income_won": 51_000_000}), sample))


def test_married_uses_married_limit():
    assert "guarantee_fee" in ids(match_policies(Profile(**{**JEONSE, "income_won": 55_000_000, "married": True}), sample))


def test_over_age_and_not_homeless_excluded():
    assert "guarantee_fee" not in ids(match_policies(Profile(**{**JEONSE, "age": 35}), sample))
    assert "guarantee_fee" not in ids(match_policies(Profile(**{**JEONSE, "homeless": False}), sample))


def test_monthly_contract_gets_monthly_policies_only():
    p = Profile(**{**JEONSE, "deposit_won": 10_000_000, "monthly_rent_won": 500_000})
    assert p.contract_type == "monthly"
    assert ids(match_policies(p, sample)) == {"monthly_rent", "any_loan"}


def test_unverifiable_condition_marks_check_needed():
    p = Profile(**{**JEONSE, "deposit_won": 10_000_000, "monthly_rent_won": 500_000})
    m = {x.policy["id"]: x for x in match_policies(p, sample)}
    assert m["monthly_rent"].status == "check_needed"
    assert any("원가구 소득" in r for r in m["monthly_rent"].reasons)


def test_null_condition_not_checked():
    p = Profile(age=70, income_won=999_000_000, married=False, homeless=False, deposit_won=0, monthly_rent_won=0)
    assert ids(match_policies(p, sample)) == {"any_loan"}


def test_real_policy_table_is_well_formed():
    real = load_policies()
    fields = set(sample[0])
    assert len(real) >= 5
    for p in real:
        assert set(p) == fields, p["id"]
        assert p["source_url"].startswith("https://") and p["checked_date"].startswith("2026-11-1")
    assert {"guarantee_fee", "seoul_youth_monthly_rent"} <= {p["id"] for p in real}


def test_real_table_jeonse_youth_gets_guarantee_fee():
    m = {x.policy["id"]: x for x in match_policies(Profile(**JEONSE), load_policies())}
    assert m["guarantee_fee"].status == "eligible"  # 반환보증 가입은 자격이 아니라 할 일


def test_newlywed_income_band_needs_check():
    m = {x.policy["id"]: x for x in match_policies(Profile(**{**JEONSE, "income_won": 70_000_000, "married": True}), sample)}
    assert m["guarantee_fee"].status == "check_needed"
    assert any("신혼부부" in r for r in m["guarantee_fee"].reasons)
    assert "guarantee_fee" not in ids(match_policies(Profile(**{**JEONSE, "income_won": 80_000_000, "married": True}), sample))


def test_semi_jeonse_gets_guarantee_fee_in_real_table():
    p = Profile(**{**JEONSE, "deposit_won": 200_000_000, "monthly_rent_won": 300_000})
    assert "guarantee_fee" in ids(match_policies(p, load_policies()))


def test_zero_deposit_never_gets_guarantee_fee():
    p = Profile(**{**JEONSE, "deposit_won": 0, "monthly_rent_won": 500_000})
    assert "guarantee_fee" not in ids(match_policies(p, load_policies()))


def test_region_only_policy_hidden_outside_its_region():
    p = Profile(**{**JEONSE, "deposit_won": 10_000_000, "monthly_rent_won": 500_000})
    assert "monthly_rent" in ids(match_policies(p, sample, region="11"))
    assert "monthly_rent" not in ids(match_policies(p, sample, region="41"))
    assert "any_loan" in ids(match_policies(p, sample, region="41"))  # regions가 비어 있으면 전국


def test_real_table_regions_by_city():
    real = load_policies()
    seoul = ids(match_policies(Profile(**JEONSE), real, region="11"))
    incheon = ids(match_policies(Profile(**JEONSE), real, region="28"))
    gyeonggi = ids(match_policies(Profile(**JEONSE), real, region="41"))
    assert "guarantee_fee" in seoul & incheon & gyeonggi  # 보증료 지원은 전국 사업
    assert "seoul_youth_deposit_interest" in seoul and "seoul_youth_deposit_interest" not in incheon | gyeonggi
    assert "incheon_youth_deposit_interest" in incheon and "incheon_youth_deposit_interest" not in seoul | gyeonggi
    assert "gyeonggi_city_youth_interest" in gyeonggi


def test_incheon_35_to_39_gets_incheon_rent_not_molit():
    p = Profile(**{**JEONSE, "age": 37, "deposit_won": 10_000_000, "monthly_rent_won": 500_000})
    got = ids(match_policies(p, load_policies(), region="28"))
    assert "incheon_youth_monthly_rent" in got and "molit_youth_monthly_rent" not in got


def test_area_limit_checked_when_area_known():
    base = {**JEONSE, "deposit_won": 200_000_000}
    real = load_policies()
    big = ids(match_policies(Profile(**base, area_m2=141.16), real, region="28"))
    assert "incheon_youth_deposit_interest" not in big
    m = {x.policy["id"]: x for x in match_policies(Profile(**base, area_m2=59.9), real, region="28")}
    reasons = m["incheon_youth_deposit_interest"].reasons
    assert "전용면적 59.9㎡ ≤ 85㎡" in reasons and not any("85㎡" in r for r in reasons if r.startswith("확인 필요"))


def test_area_limit_needs_check_when_area_unknown():
    m = {x.policy["id"]: x for x in match_policies(Profile(**{**JEONSE, "deposit_won": 200_000_000}), load_policies(), region="28")}
    assert any("85㎡" in r for r in m["incheon_youth_deposit_interest"].reasons if r.startswith("확인 필요"))
