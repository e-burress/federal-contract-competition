"""A fake USAspending for the offline tests: the download service, its status page, the file server and the transaction count service.

It knows the "true" transactions of every day (rows_per_day rows per day and agency) and can misbehave the way the real service did in
November 2026:
  cutoff      windows (start, end) whose file comes back CUT OFF: status "finished", a good ZIP, but only half the rows, every time
              the same window is asked for (the real service seemed to keep the generated file)
  big         windows the service reports as too big (500,000 rows)
  count_down  the count service does not answer
  drift       rows added to every count (records that changed after the file was made)
"""
import csv
import io
import zipfile
from datetime import date, timedelta
from unittest import mock


def days(start, end):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        yield d.isoformat()
        d += timedelta(days=1)


class FakeService:
    def __init__(self, rows_per_day=2, cutoff=(), big=(), count_down=False, drift=0, fail_download=False):
        self.rows_per_day, self.cutoff, self.big = rows_per_day, set(cutoff), set(big)
        self.count_down, self.drift, self.fail_download = count_down, drift, fail_download
        self.jobs, self.submits, self.counts, self.downloads = {}, [], [], []

    # ---- the data ------------------------------------------------------------------------------------------------------
    def truth(self, filters):
        tp = filters["time_period"][0]
        names = [a["name"] for a in filters["agencies"]]
        rows = []
        for d in days(tp["start_date"], tp["end_date"]):
            for ag in names:
                ai = "".join(w[0] for w in ag.split())          # agency initials keep the keys of different agencies apart
                for i in range(self.rows_per_day):
                    rows.append({"contract_transaction_unique_key": f"T{d}-{ai}-{i}", "contract_award_unique_key": f"A{d}-{ai}-{i}",
                                 "award_id_piid": f"P{d}{ai}{i}", "modification_number": "0", "action_date": d,
                                 "award_type_code": "D", "award_or_idv_flag": "AWARD", "awarding_agency_name": ag,
                                 "extent_competed_code": "A", "number_of_offers_received": "1" if i % 2 == 0 else "3",
                                 "federal_action_obligation": "300000", "base_and_all_options_value": "300000",
                                 "base_and_exercised_options_value": "300000", "action_type_code": "",
                                 "solicitation_identifier": f"S{d}{ai}{i}"})
        return rows

    def delivered(self, body):
        rows = self.truth(body["filters"])
        tp = body["filters"]["time_period"][0]
        if (tp["start_date"], tp["end_date"]) in self.cutoff:
            rows = rows[::2]                                   # a cut-off file: rows of every date, but only half of them
        return rows

    def window(self, body):
        tp = body["filters"]["time_period"][0]
        return tp["start_date"], tp["end_date"]

    # ---- the endpoints -------------------------------------------------------------------------------------------------
    def http_json(self, url, method="GET", body=None, headers=None, ua=None, **kw):
        if url.endswith("/download/transactions/"):
            self.submits.append(self.window(body))
            job = f"job{len(self.jobs)}"
            self.jobs[job] = body
            return {"file_name": job + ".zip", "file_url": "https://example.invalid/" + job + ".zip"}
        if "/download/status?file_name=" in url:
            body = self.jobs[url.split("file_name=")[1][:-4]]
            n = 500_000 if self.window(body) in self.big else len(self.delivered(body))
            return {"status": "finished", "message": None, "total_rows": n, "total_size": 10.0, "seconds_elapsed": "1.0"}
        if url.endswith("/search/spending_by_transaction_count/"):
            self.counts.append(self.window(body))
            if self.count_down:
                return None
            return {"results": {"contracts": len(self.truth(body["filters"])) + self.drift, "idvs": 0, "grants": 0, "loans": 0,
                                "direct_payments": 0, "other": 0}}
        raise AssertionError(f"unexpected request {url}")

    def download_file(self, url, dest, ua=None, **kw):
        if self.fail_download:
            return None
        body = self.jobs[url.rsplit("/", 1)[1][:-4]]
        self.downloads.append(self.window(body))
        cols = body["columns"]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(cols)
        for r in self.delivered(body):
            w.writerow([r.get(c, "") for c in cols])
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("Contracts_PrimeTransactions_2026-11-10_H10M00S00_1.csv", buf.getvalue())
            z.writestr("Contracts_Subawards_2026-11-10_H10M00S00.csv", "")
        return len(buf.getvalue())

    def patch(self, dl):
        """patch the shared download module `dl` (01_download_usaspending.py) to talk to this fake; no pauses"""
        return [mock.patch.object(dl, "http_json", side_effect=self.http_json),
                mock.patch.object(dl, "download_file", side_effect=self.download_file),
                mock.patch.object(dl, "PAUSE_SECONDS", 0), mock.patch.object(dl, "POLL_SECONDS", 0)]
