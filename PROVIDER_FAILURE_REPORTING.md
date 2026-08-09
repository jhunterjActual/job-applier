# Provider Failure Reporting

CareerTrellis uses an explicit, review-before-sharing workflow for reporting repeated job-source failures. It never sends source diagnostics to the project maintainers in the background.

## User workflow

1. CareerTrellis stores an allowlisted, bounded local diagnostic record when a supported provider produces an actionable source warning.
2. The user opens **Source diagnostics** and chooses **Prepare Maintainer Report**.
3. CareerTrellis builds and displays the complete report locally. Preparing or copying it makes no network request to a maintainer.
4. The user may copy the report or choose **Open GitHub Issue Draft**. That explicit link sends the displayed report to `github.com` as a prefilled, still-unsubmitted issue draft.
5. The user can edit or abandon the draft. GitHub receives nothing from this workflow unless the user opens the external draft, and the CareerTrellis maintainers receive nothing unless the user submits it there.

## Report contract

Report format version 1 contains only:

- the allowlisted application build identifier;
- calendar dates, without precise event times;
- the supported-provider identifier and actionable diagnostic code;
- bounded occurrence counts; and
- the latest bounded aggregate source counters for each provider/code group.

The report generator independently reapplies provider, diagnostic-code, build, date, counter-name, and counter-value allowlists. It does not pass through additional database or API fields.

The report excludes:

- profile, resume, cover-letter, interview, contact, and application content;
- API keys, credentials, and provider configuration;
- search terms and locations;
- employer names, job titles, descriptions, URLs, and URL fingerprints;
- generated materials and local file paths;
- raw exceptions, error messages, response bodies, headers, and browsing details; and
- device, operating-system, network, or user identifiers.

Informational stale-posting and partial-result records are not reportable. The report is limited to URL/data format drift, access challenges, and provider failures that may require maintainer attention.

## Trust boundary

The local preview endpoint is read-only and returns `Cache-Control: no-store`. It does not contact GitHub or any telemetry service. The browser constructs a fixed `https://github.com/jhunterjActual/job-applier/issues/new` link only after the user requests and reviews a report; the displayed Markdown is the exact body placed in that link.

This mechanism deliberately avoids persistent reporting consent, automatic retries, background submission, installation identifiers, and silent telemetry. A future hosted ingestion service would require a separate privacy and security review, explicit per-report consent, authenticated transport, abuse controls, retention limits, deletion procedures, public schema documentation, and tests proving that the payload cannot expand beyond this allowlist.

## Maintainer handling

Maintainers should treat submitted reports as public issue content, avoid asking users to paste unrestricted exports, and request the minimum additional information needed. If a report is no longer useful, normal GitHub issue retention and deletion controls apply; CareerTrellis retains only its existing bounded local history until the user clears it.
