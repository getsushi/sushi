"""Matching tests.

Location scoring gets the most coverage because it is the field that decides
whether a match is useful or a waste of the user's time, and it is the one that
has already shipped a bug: "Remote" was being treated as a place, so a user in
California was shown Berlin-only roles as local.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi.discovery import (countries_in, countries_in_place,  # noqa: E402
                             location_fit, score_role)
from sushi.models import Profile, Role  # noqa: E402

US = Profile(country="United States", target_locations=["United States", "San Francisco", "Remote"],
             remote_ok=True, seniority="director",
             target_titles=["Director of Engineering", "Engineering Manager"],
             skills=["platform", "kubernetes"])

UK = Profile(country="United Kingdom", target_locations=["United Kingdom", "London", "Remote"],
             remote_ok=True, seniority="exec",
             target_titles=["CTO", "VP Engineering", "Head of AI"], skills=["ai", "llm"])


def check(name: str, got: object, want: object) -> bool:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    return ok


def test_locations() -> bool:
    print("location_fit, US-based user:")
    ok = True
    ok &= check("Remote - USA", location_fit("Remote - USA", US), 3)
    ok &= check("San Francisco", location_fit("San Francisco, California", US), 3)
    # The bug: 'Remote' as a target must not make foreign remote roles local.
    ok &= check("Germany (Remote)", location_fit("Germany (Remote)", US), -3)
    ok &= check("Republic of Ireland (Remote)", location_fit("Republic of Ireland (Remote)", US), -3)
    ok &= check("London, UK", location_fit("London, United Kingdom", US), -3)
    ok &= check("Anywhere", location_fit("Anywhere", US), 2)
    ok &= check("Remote (no country)", location_fit("Remote", US), 1)
    ok &= check("empty", location_fit("", US), 0)

    print("location_fit, UK-based user:")
    ok &= check("Remote, United Kingdom", location_fit("Remote, United Kingdom", UK), 3)
    ok &= check("London", location_fit("London", UK), 3)
    ok &= check("Remote - USA", location_fit("Remote - USA", UK), -3)
    ok &= check("Germany (Remote)", location_fit("Germany (Remote)", UK), -3)
    return bool(ok)


#: What the first-run location step produces: a cluster, a country, or
#: "Worldwide" for someone who only wants remote-from-anywhere work.
EMEA = Profile(country="", target_locations=["EMEA"], seniority="ic",
               target_titles=["Data Analyst"])
APAC = Profile(country="", target_locations=["APAC"], seniority="ic",
               target_titles=["Data Analyst"])
GERMANY = Profile(country="Germany", target_locations=["Germany"],
                  seniority="ic", target_titles=["Data Analyst"])
WORLD = Profile(country="", target_locations=["Worldwide"], workplace="remote",
                seniority="ic", target_titles=["Data Analyst"])


def test_clusters() -> bool:
    """A region picked as a *preference* has to match jobs that name a country.

    This is the direction that did not work: regions were only understood on
    the job's side, so choosing EMEA matched nothing, because no vacancy is
    ever advertised as being in "EMEA".
    """
    print("clusters:")
    ok = True
    ok &= check("EMEA <- Berlin", location_fit("Berlin, Germany", EMEA), 3)
    ok &= check("EMEA <- Dubai", location_fit("Dubai, UAE", EMEA), 3)
    ok &= check("EMEA <- Cape Town", location_fit("Cape Town, South Africa", EMEA), 3)
    ok &= check("EMEA not San Francisco", location_fit("San Francisco, CA", EMEA), -3)
    ok &= check("APAC <- Singapore", location_fit("Singapore", APAC), 3)
    ok &= check("APAC <- Tokyo", location_fit("Tokyo, Japan", APAC), 3)
    ok &= check("APAC not Berlin", location_fit("Berlin, Germany", APAC), -3)
    # And the original direction still holds.
    ok &= check("Germany <- EMEA-wide posting", location_fit("Remote, EMEA", GERMANY), 3)
    ok &= check("Germany <- Europe-wide posting", location_fit("Remote (Europe)", GERMANY), 3)
    return bool(ok)


#: Picked the region, then picked a country inside it — which is the only
#: way the first-run step lets you name a country at all.
APAC_PK = Profile(country="", target_locations=["APAC", "Pakistan"],
                  seniority="ic", target_titles=["Data Analyst"])
#: Two regions, one of them narrowed. The other stays whole.
APAC_EU_DE = Profile(country="", target_locations=["APAC", "Europe", "Germany"],
                     seniority="ic", target_titles=["Data Analyst"])


def test_country_narrows_its_region() -> bool:
    """A country named inside a region narrows it, it does not widen it.

    Picking APAC and then Pakistan was read as "APAC or Pakistan" — a union,
    which is the region back again — so the top of the feed came back
    Singapore, India and the Philippines for someone who had just said
    Pakistan.
    """
    print("country narrows region:")
    ok = True
    ok &= check("APAC+PK <- Karachi", location_fit("Karachi, Pakistan", APAC_PK), 3)
    ok &= check("APAC+PK not Singapore", location_fit("Remote - Singapore", APAC_PK), -3)
    ok &= check("APAC+PK not India", location_fit("Remote - India", APAC_PK), -3)
    ok &= check("APAC+PK not Philippines", location_fit("Remote, Philippines", APAC_PK), -3)
    # The un-narrowed region is untouched: no country was named inside APAC.
    ok &= check("APAC+EU+DE <- Singapore", location_fit("Singapore", APAC_EU_DE), 3)
    ok &= check("APAC+EU+DE <- Berlin", location_fit("Berlin, Germany", APAC_EU_DE), 3)
    ok &= check("APAC+EU+DE not Paris", location_fit("Paris, France", APAC_EU_DE), -3)
    # A country outside every region picked is a union, not a narrowing.
    emea_pk = Profile(country="", target_locations=["EMEA", "Pakistan"],
                      seniority="ic", target_titles=["Data Analyst"])
    ok &= check("EMEA+PK <- Berlin", location_fit("Berlin, Germany", emea_pk), 3)
    ok &= check("EMEA+PK <- Karachi", location_fit("Karachi, Pakistan", emea_pk), 3)
    return bool(ok)


def test_where_to_look_filters() -> bool:
    """A country you did not ask for is left out, not merely marked down.

    -3 was a deduction, and a strong title, the right seniority and two
    matching skills paid it off with points to spare — so "Remote - US
    Only" cleared the feed's bar for someone who had asked for Saudi
    Arabia, and sat at the top of All jobs on the strength of its title.
    """
    print("where to look filters:")
    saudi = Profile(country="", target_locations=["Middle East / Africa", "Saudi Arabia"],
                    workplace="remote", seniority="ic", skills=["Python", "SQL"],
                    target_titles=["Senior Data Analyst"])

    def shown(location: str) -> bool:
        role = Role(title="Senior Data Analyst", location=location)
        return score_role(role, saudi)[0] >= 8

    ok = True
    ok &= check("not shown: Remote - US Only", shown("Remote - US Only"), False)
    ok &= check("not shown: United States, Remote", shown("United States, Remote"), False)
    ok &= check("not shown: Dubai (not asked for)", shown("Dubai, UAE"), False)
    # "US Fully Remote" says fully remote and says US. The US decides.
    ok &= check("not shown: US Fully Remote", shown("US Fully Remote"), False)
    ok &= check("not shown: Anywhere in the US", shown("Remote (US) - Anywhere in the US"), False)
    # What a stated country must never cost: the roles genuinely open to them.
    ok &= check("shown: Remote - Saudi Arabia", shown("Remote - Saudi Arabia"), True)
    ok &= check("shown: Riyadh", shown("Riyadh, Saudi Arabia"), True)
    ok &= check("shown: Remote, Global", shown("Remote, Global"), True)
    ok &= check("shown: Remote | Worldwide", shown("Remote | Worldwide"), True)
    # Unplaceable is not wrong, only unknown.
    ok &= check("shown: Remote (no country)", shown("Remote"), True)
    # And someone who has stated nothing is still shown everything.
    anyone = Profile(country="", target_locations=[], seniority="ic",
                     skills=["Python", "SQL"], target_titles=["Senior Data Analyst"])
    ok &= check("no answer: US job still shown",
                score_role(Role(title="Senior Data Analyst",
                                location="Remote - US Only"), anyone)[0] >= 8, True)
    return bool(ok)


def test_unplaceable_is_not_a_free_pass() -> bool:
    """A place the resolver cannot name is a wrong place, not an unknown one.

    No list of city names is ever complete, and "Santa Clara, California",
    "Des Moines" and "Medellín" all resolved to nothing — which scored -1,
    "somewhere unplaceable", which a matching title and the right seniority
    paid off without noticing. That is how a feed headed "Showing roles in
    Pakistan" filled up with California.
    """
    print("unplaceable:")
    pk = Profile(country="", target_locations=["APAC", "Pakistan"],
                 seniority="ic", skills=["Python", "SQL"],
                 target_titles=["Operations Analyst"])

    def shown(location: str) -> bool:
        return score_role(Role(title="Operations Analyst",
                               location=location), pk)[0] >= 8

    ok = True
    for where in ("Santa Clara, California", "Des Moines", "Medellín",
                  "Bioggio, ch", "Arlington, VA", "Southaven, MS; Memphis, TN"):
        ok &= check(f"not shown: {where}", shown(where), False)
    # "Remote" names nowhere, which is a different thing from naming
    # somewhere unrecognised, and must still reach someone.
    ok &= check("shown: Remote", shown("Remote"), True)
    ok &= check("shown: Remote - Anywhere", shown("Remote - Anywhere"), True)
    ok &= check("shown: Karachi", shown("Karachi"), True)
    ok &= check("shown: Islamabad", shown("Islamabad"), True)
    return bool(ok)


def test_states_and_cities_resolve() -> bool:
    """The fifty states, their abbreviations, and the cities people live in.

    Half the corpus writes an address rather than a country, and the strict
    rule above is only fair if the resolver has a real go at reading one.
    """
    print("states and cities:")
    ok = True
    ok &= check("California is the US", "US" in countries_in_place("Santa Clara, California"), True)
    ok &= check("VA is the US", "US" in countries_in_place("Arlington, VA"), True)
    ok &= check("NC is the US", "US" in countries_in_place("Fort Liberty, NC"), True)
    # Only as a fallback: a line that names a country is read as that country.
    ok &= check("Toronto, Canada is Canada",
                countries_in_place("Toronto, Canada"), {"CA"})
    ok &= check("Manchester is the UK", "UK" in countries_in_place("Manchester"), True)
    ok &= check("Gurugram is India", "IN" in countries_in_place("Gurugram"), True)
    ok &= check("Islamabad is Pakistan", "PK" in countries_in_place("Islamabad"), True)
    ok &= check("Medellín is Colombia", "CO" in countries_in_place("Medellín"), True)
    # And a bare two-letter word in prose is still not a state.
    ok &= check("prose is left alone", countries_in("we value in-person work"), set())
    return bool(ok)


def test_cities_resolve_to_countries() -> bool:
    """Boards post a city, not a country, so a city has to place the job.

    Picking "United Kingdom" on first run and then being shown London roles
    as unplaceable would make the whole step look broken.
    """
    print("cities:")
    ok = True
    ok &= check("UK <- London", location_fit("London", UK), 3)
    ok &= check("Germany <- Munich", location_fit("Munich", GERMANY), 3)
    ok &= check("Germany not Toronto", location_fit("Toronto", GERMANY), -3)
    ok &= check("APAC <- Bengaluru", location_fit("Bengaluru", APAC), 3)
    return bool(ok)


def test_remote_anywhere() -> bool:
    """"Remote, anywhere" is a different answer from "remote, from here"."""
    print("remote:")
    ok = True
    ok &= check("Worldwide <- Anywhere", location_fit("Remote - Anywhere", WORLD), 2)
    ok &= check("Worldwide <- Worldwide", location_fit("Worldwide", WORLD), 2)
    # A country-locked remote job is not open to someone who said anywhere.
    ok &= check("Worldwide not Remote US", location_fit("Remote - USA", WORLD), -3)
    # Where the country is the one they named, it is.
    ok &= check("Germany <- Remote Germany", location_fit("Remote - Germany", GERMANY), 3)
    return bool(ok)


def test_seniority() -> bool:
    print("seniority filtering:")
    ok = True
    junior = Role(title="Junior Software Engineer", location="Remote - USA")
    s, _ = score_role(junior, US)
    ok &= check("junior role scores low for a director", s < 8, True)

    intern = Role(title="Engineering Intern", location="Remote - USA")
    s, _ = score_role(intern, US)
    ok &= check("intern filtered out entirely", s, 0)

    good = Role(title="Director of Engineering, Platform", location="Remote - USA",
                department="Platform")
    s, why = score_role(good, US)
    ok &= check("director role scores well", s >= 10, True)
    ok &= check("explains itself", len(why) > 0, True)
    return bool(ok)


def test_profile_drives_results() -> bool:
    """The same vacancy must score differently for different people."""
    print("profile drives the score:")
    role = Role(title="VP Engineering, AI Platform", location="Remote, United Kingdom",
                department="AI")
    uk_score, _ = score_role(role, UK)
    us_score, _ = score_role(role, US)
    ok = check("UK exec scores it high", uk_score >= 11, True)
    ok &= check("US director scores it lower", us_score < uk_score, True)
    return bool(ok)


if __name__ == "__main__":
    results = [test_locations(), test_clusters(), test_country_narrows_its_region(),
               test_where_to_look_filters(), test_unplaceable_is_not_a_free_pass(),
               test_states_and_cities_resolve(), test_cities_resolve_to_countries(),
               test_remote_anywhere(), test_seniority(), test_profile_drives_results()]
    print(f"\n{'ALL TESTS PASSED' if all(results) else 'FAILURES ABOVE'}")
    raise SystemExit(0 if all(results) else 1)
