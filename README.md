# Fed Speak Tracker

A self-updating, single-page tracker of speeches, testimony, and statements by Federal Reserve officials: the seven members of the Board of Governors, the 12 Reserve Bank presidents, and other Federal Reserve officials (e.g., New York Fed Markets Group leadership, Reserve Bank research and supervision officers, Board staff testifying before Congress).

The page lists every item in reverse chronological order, grouped by date, with filters for institution, official type, remark type, topic, period, and 2026 FOMC voters. A strip at the top shows each FOMC participant's days since last remarks on record. A combined RSS feed (`feed.xml`) is published alongside the page for use in Outlook, Slack, or an RSS reader.

The Federal Reserve previously offered a comparable system-wide resource, the St. Louis Fed's "FOMC Speak" database, which was retired on December 31, 2023 (archive: https://fraser.stlouisfed.org/timeline/fomc-speak-archive). This project rebuilds that capability from current official sources.

## How it works

`scripts/build.py` (Python standard library only; no third-party packages) runs on a schedule in GitHub Actions:

1. Fetches each source in `config/sources.json`.
2. Parses it with the matching adapter (Board RSS, Reserve Bank RSS, Fed in Print RSS, or a Reserve Bank HTML listing page).
3. Resolves each speaker against `config/roster.json` (name, role, institution, FOMC voter status). Officials not on the roster are kept and classified as "Other Fed officials."
4. Tags topics using the keyword rules in `config/topics.json`.
5. Merges into the persistent archive `data/items.json`. When the same remarks appear in two sources, the more primary source (lower `priority`) is linked first and the other is kept as an alternate link.
6. Writes `data/health.json` (per-source status, item count, newest item, stale flag) and renders `site/index.html` and `site/feed.xml`.

A failing source never breaks the build; it is reported in the "Sources and method" table at the bottom of the page.

## Coverage matrix (verified October 6, 2026)

| Institution | Primary source | Secondary source | Notes |
|---|---|---|---|
| Board of Governors | federalreserve.gov `speeches_and_testimony.xml` (official RSS) | Fed in Print: Board | Includes Board staff testimony. |
| New York | Fed in Print: New York | | Includes Markets Group and other officers' speeches. |
| Dallas | dallasfed.org/rss/speeches (official RSS) | Fed in Print: Dallas | |
| Atlanta | atlantafed.org/rss/speechindex (official RSS) | Fed in Print: Atlanta | Interim president since March 1, 2026; sparse volume expected. |
| Boston | bostonfed.org rss_speeches.xml (official RSS) | Fed in Print: Boston | Official feed's newest item was November 15, 2024 at verification; will show as stale. Fed in Print is the effective source. |
| Chicago | Chicago Fed president's speaking page (HTML listing) | Fed in Print: Chicago | Dates parsed from the speech URL. Fed in Print holds comparatively few Chicago speeches. |
| Kansas City | kansascityfed.org/speeches/ (HTML listing) | Fed in Print: Kansas City | Speech URLs carry no date; the tracker records the date it first saw the item and labels it as such. |
| Philadelphia, Cleveland, Richmond, St. Louis, Minneapolis, San Francisco | Fed in Print (per-Bank RSS) | | Each Bank deposits its speech series in Fed in Print. |

Fed in Print (https://www.fedinprint.org/rss) is the Federal Reserve System's publication index maintained by the Federal Reserve Bank of St. Louis. Its per-Bank RSS feeds mix research with speeches; the tracker keeps only items whose series is a speech, testimony, remarks, or statement series.

## Setup (GitHub Pages, about 15 minutes)

1. Create a new GitHub repository and upload the contents of this folder (keep the `.github` folder; it is hidden on some systems).
2. In `config/sources.json`, replace `contact` with a monitored email address. It is sent in the User-Agent header so Federal Reserve web teams can reach you, which is standard courtesy for automated retrieval.
3. Repository Settings > Pages > Build and deployment > Source: select **GitHub Actions**.
4. Repository Settings > Actions > General > Workflow permissions: select **Read and write permissions**.
5. Actions tab > "Update Fed speak tracker" > **Run workflow**. The first run takes roughly one to two minutes. The page URL appears in the deploy job and under Settings > Pages.

The schedule runs every 30 minutes during U.S. business hours on weekdays and every six hours otherwise. GitHub may delay scheduled runs during periods of high load; use "Run workflow" when you need an immediate refresh (for example, right after an FOMC blackout period ends).

Hosting considerations for institutional use: GitHub Pages sites are publicly reachable unless your organization uses GitHub Enterprise Cloud with private Pages. The content is public-domain Federal Reserve material, but if your firm restricts external hosting, the same build runs on any internal server via cron (`python3 scripts/build.py`, then serve the `site/` folder). GitHub may also disable scheduled workflows in public repositories after 60 days without repository activity; the archive commits normally count as activity, but check the Actions tab periodically.

## Run locally

```bash
python3 -m unittest discover -s tests -v   # offline tests against saved payloads
python3 scripts/build.py                   # live fetch, update archive, render site/
python3 -m http.server -d site 8000        # open http://localhost:8000
```

## Maintenance calendar

| When | Action | File |
|---|---|---|
| Any appointment or departure (e.g., a permanent Atlanta Fed president) | Add or update the person; set `"active": false` for departures so historical items keep correct labels. | `config/roster.json` |
| First FOMC meeting each January | Update `voter_year` and each president's `voter` flag for the new rotation. | `config/roster.json` |
| A source shows "Error" or "Stale" for more than a week | Check whether the Bank changed its feed or page structure; update the URL or `link_pattern`. | `config/sources.json` |
| As needed | Adjust topic keyword rules. | `config/topics.json` |

Archived items automatically pick up roster changes (role titles, voter status) on the next build.

## Known limitations

- Media interviews and podcasts without an official transcript are generally not captured. Fed in Print and some Bank sites include some of them.
- Fed in Print can lag a Bank's own site by days. For the six Banks covered only through Fed in Print, this is the main timeliness gap; adding a direct HTML adapter for a Bank (see the Chicago and Kansas City entries) closes it.
- HTML listing adapters depend on page structure. The health table will show when one stops returning items.
- Topic tags are keyword-based navigation aids. They are not a classification of policy stance, and the tracker deliberately does not label officials as hawkish or dovish.
- The roster reflects information verified on October 6, 2026. Verify Jerome Powell's continued service as a governor before relying on that entry (he stated in May 2026 that he would keep his seat after his term as Chair ended).

## Sources consulted for this configuration

- Board RSS feeds: https://www.federalreserve.gov/feeds
- Fed in Print RSS directory: https://www.fedinprint.org/rss
- Dallas Fed RSS: https://www.dallasfed.org/rss/
- Atlanta Fed speech feed: https://www.atlantafed.org/rss/speechindex
- Boston Fed speech feed: https://www.bostonfed.org/feeds/rss_speeches.xml
- 2026 FOMC membership: FOMC minutes, January 27-28, 2026, https://www.federalreserve.gov/monetarypolicy/files/fomcminutes20260128.pdf
- Board leadership: Congressional Research Service, "Federal Reserve Board: Current and Historical Membership" (R48233, updated May 27, 2026)
- Atlanta Fed interim leadership: https://www.atlantafed.org/who-we-are/people/executive-leadership-committee/cheryl-venable
- FOMC Speak retirement: https://fraser.stlouisfed.org/timeline/fomc-speak-archive
