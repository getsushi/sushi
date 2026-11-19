"""Work out the profile answers a person never typed, from what they did.

A phone number says which country someone is in; a location says the same
more precisely; the country, set against where the job is, settles "are you
allowed to work here" and "will you need sponsorship"; seniority and country
give a sensible salary figure. Every one of these is a guess and is planned
with ``source="guess"``, so it is outlined amber in the form and listed on
Home for the user to confirm. Anything the user has set in their profile
always wins over this.
"""
from __future__ import annotations

import re

from .models import Profile, Role

#: Dial code -> (country code as discovery uses it, country name, currency).
_DIAL = {
    "44": ("UK", "United Kingdom", "£"), "1": ("US", "United States", "$"),
    "92": ("PK", "Pakistan", "PKR "), "91": ("IN", "India", "INR "),
    "971": ("AE", "United Arab Emirates", "AED "), "966": ("SA", "Saudi Arabia", "SAR "),
    "974": ("QA", "Qatar", "QAR "), "49": ("DE", "Germany", "€"),
    "33": ("FR", "France", "€"), "31": ("NL", "Netherlands", "€"),
    "353": ("IE", "Ireland", "€"), "34": ("ES", "Spain", "€"),
    "39": ("IT", "Italy", "€"), "61": ("AU", "Australia", "A$"),
    "64": ("NZ", "New Zealand", "NZ$"), "65": ("SG", "Singapore", "S$"),
    "46": ("SE", "Sweden", "SEK "), "47": ("NO", "Norway", "NOK "),
    "45": ("DK", "Denmark", "DKK "), "41": ("CH", "Switzerland", "CHF "),
    "48": ("PL", "Poland", "PLN "), "351": ("PT", "Portugal", "€"),
    "880": ("BD", "Bangladesh", "BDT "), "234": ("NG", "Nigeria", "NGN "),
    "27": ("ZA", "South Africa", "ZAR "), "20": ("EG", "Egypt", "EGP "),
    "90": ("TR", "Turkey", "TRY "), "55": ("BR", "Brazil", "BRL "),
}
_BY_CODE = {code: (name, cur) for code, name, cur in _DIAL.values()}

#: Mid-level software salary per year, local currency. Rough on purpose —
#: it is a starting figure for the user to correct, never a fact.
_MID_SALARY = {
    "UK": 55_000, "US": 120_000, "PK": 3_000_000, "IN": 1_800_000,
    "AE": 300_000, "SA": 300_000, "QA": 300_000, "DE": 65_000, "FR": 50_000,
    "NL": 60_000, "IE": 65_000, "ES": 40_000, "IT": 38_000, "AU": 120_000,
    "NZ": 100_000, "SG": 90_000, "SE": 550_000, "NO": 650_000, "DK": 550_000,
    "CH": 110_000, "PL": 180_000, "PT": 30_000, "BD": 1_200_000,
    "NG": 8_000_000, "ZA": 600_000, "EG": 400_000, "TR": 1_000_000,
    "BR": 150_000,
}
_SENIORITY = {"junior": 0.65, "ic": 1.0, "mid": 1.0, "senior": 1.35,
              "lead": 1.5, "manager": 1.55, "director": 2.0, "exec": 2.5}


def country_from_phone(phone: str) -> str:
    """Country code for an international number, longest prefix first."""
    digits = re.sub(r"\D", "", phone or "")
    if not (phone or "").strip().startswith(("+", "00")):
        return ""
    digits = digits[2:] if phone.strip().startswith("00") else digits
    for n in (3, 2, 1):
        hit = _DIAL.get(digits[:n])
        if hit:
            return hit[0]
    return ""


def home_country(profile: Profile) -> str:
    from .discovery import countries_in, countries_in_place
    for text in (profile.country, profile.location):
        found = countries_in(text or "") or countries_in_place(text or "")
        if len(found) == 1:
            return next(iter(found))
    return country_from_phone(profile.phone)


def _job_country(role: Role | None, home: str) -> str:
    from .discovery import countries_in_place
    if role is None:
        return home
    found = countries_in_place(getattr(role, "location", "") or "")
    if len(found) == 1:
        return next(iter(found))
    # Remote with no country named, or several: treat as where you are.
    return home


def _salary(profile: Profile, role: Role | None, home: str) -> str:
    advertised = (getattr(role, "salary", "") or "").strip() if role else ""
    if advertised:
        return advertised
    base = _MID_SALARY.get(home)
    if not base:
        return ""
    level = (profile.seniority or "").lower()
    factor = _SENIORITY.get(level, 1.0)
    if not level:
        try:
            years = float(re.findall(r"\d+(?:\.\d+)?", profile.years_experience or "")[0])
            factor = 0.65 if years < 2 else 1.0 if years < 5 else 1.35 if years < 9 else 1.5
        except (IndexError, ValueError):
            pass
    value = round(base * factor, -3)
    cur = _BY_CODE.get(home, ("", ""))[1]
    return f"{cur}{value:,.0f}"


def inferred_answers(profile: Profile, role: Role | None = None) -> dict[str, str]:
    """Profile keys the user left blank, filled in from what they did give."""
    if profile is None:
        return {}
    out: dict[str, str] = {}
    home = home_country(profile)
    if not home:
        return out
    name = _BY_CODE.get(home, (profile.country or "", ""))[0]
    if not profile.country and name:
        out["country"] = name
    if not profile.location and name:
        out["location"] = name
    job = _job_country(role, home)
    same = job == home
    if not profile.work_authorisation:
        out["work_authorisation"] = "Yes" if same else "No"
    if not profile.requires_sponsorship:
        out["requires_sponsorship"] = "No" if same else "Yes"
    if not getattr(profile, "notice_period", ""):
        out["notice_period"] = "1 month"
    if not profile.salary_expectation:
        pay = _salary(profile, role, job)
        if pay:
            out["salary_expectation"] = pay
    return out
