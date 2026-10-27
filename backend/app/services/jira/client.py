"""Typed Jira Cloud REST API v3 (+ Agile 1.0) client.

Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
Search: /rest/api/3/search/jql (new) with fallback to /rest/api/3/search.
Agile boards/sprints/rank: /rest/agile/1.0/...

All methods raise JiraError with a machine-readable ``kind`` so callers can
distinguish auth / permission / rate-limit / network / not-found failures
without parsing message text.
"""
from __future__ import annotations

import asyncio
import base64
import random
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx


class JiraError(Exception):
    def __init__(self, kind: str, message: str, status: int = 0,
                 retry_after: float = 0.0):
        super().__init__(message)
        self.kind = kind  # auth|permission|not_found|rate_limited|network|validation|server|conflict
        self.status = status
        self.retry_after = retry_after


KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,19}-\d{1,8})\b")


def extract_key(text: str) -> str | None:
    m = KEY_RE.search(text or "")
    return m.group(1) if m else None


def normalize_base_url(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raise JiraError("validation", "Jira base URL is required.")
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raise JiraError("validation", "Jira base URL must start with http(s)://.")
    parts = urlparse(raw)
    if parts.scheme != "https":
        # Allow http only for loopback (local test doubles).
        host = (parts.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise JiraError("validation",
                            "Refusing non-HTTPS Jira URL (only loopback http is allowed).")
    if not parts.hostname:
        raise JiraError("validation", "Jira base URL has no host.")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise JiraError("validation", "Do not embed credentials in the Jira URL.")
    path = parts.path or ""
    # Reject URLs pointing at unexpected destinations (API paths, non-Jira hosts ok).
    for bad in ("/rest/", "/wiki", "/confluence"):
        if bad in path:
            raise JiraError("validation",
                            f"Jira base URL should be the site root, not a path containing '{bad}'.")
    return f"{parts.scheme}://{parts.netloc}"


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.base_url = normalize_base_url(base_url)
        self.email = (email or "").strip()
        if not self.email or "@" not in self.email:
            raise JiraError("validation", "A valid Atlassian account email is required.")
        if not api_token:
            raise JiraError("auth", "API token is required.")
        creds = base64.b64encode(f"{self.email}:{api_token}".encode()).decode()
        self._headers = {"Authorization": f"Basic {creds}",
                         "Accept": "application/json",
                         "Content-Type": "application/json",
                         "User-Agent": "atlas/1.0"}
        self._agile_headers = dict(self._headers)
        self.timeout = timeout
        self._transport = transport
        self._search_path: str | None = None  # detected working search endpoint

    # -- low level ---------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self._headers,
                                 timeout=self.timeout, transport=self._transport,
                                 follow_redirects=True)

    @staticmethod
    def _classify(status: int, body: str) -> str:
        if status in (401, 403):
            return "auth" if status == 401 else "permission"
        if status == 404:
            return "not_found"
        if status == 429:
            return "rate_limited"
        if status == 409:
            return "conflict"
        if status in (400, 405, 410, 422):
            return "validation"
        return "server" if status >= 500 else "validation"

    async def _request(self, method: str, path: str, *,
                       params: dict | None = None, json: Any = None,
                       headers: dict | None = None,
                       max_retries: int = 4) -> Any:
        attempt = 0
        delay = 1.0
        last: Exception | None = None
        while attempt <= max_retries:
            try:
                async with self._client() as c:
                    r = await c.request(method, path, params=params, json=json,
                                        headers=headers or None)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last = JiraError("network", f"Network error reaching Jira: {exc}")
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, 16.0)
                attempt += 1
                continue
            if r.status_code == 429 or r.status_code in (502, 503, 504):
                retry_after = 0.0
                try:
                    retry_after = float(r.headers.get("Retry-After", "0"))
                except ValueError:
                    retry_after = 0.0
                wait = retry_after or (delay + random.uniform(0, 0.5))
                if attempt == max_retries:
                    raise JiraError("rate_limited" if r.status_code == 429 else "server",
                                    f"Jira HTTP {r.status_code} after retries.",
                                    r.status_code, wait)
                await asyncio.sleep(min(wait, 30.0))
                delay = min(delay * 2, 16.0)
                attempt += 1
                continue
            if r.status_code >= 400:
                body = r.text[:2000]
                raise JiraError(self._classify(r.status_code, body),
                                f"Jira HTTP {r.status_code}: {body or r.reason_phrase}",
                                r.status_code)
            if r.status_code == 204 or not r.content:
                return None
            ctype = r.headers.get("content-type", "")
            if "application/json" not in ctype and method == "GET" and path.startswith("/rest/api/3/attachment"):
                return r.content
            try:
                return r.json()
            except ValueError:
                return r.text
        raise last or JiraError("network", "Jira request failed.")

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    # -- connection ---------------------------------------------------------
    async def validate(self) -> dict:
        """Full connection check: reachability + auth + identity + permissions."""
        me = await self._get("/rest/api/3/myself")
        perms = await self._get("/rest/api/3/mypermissions",
                               params={"permissions": "CREATE_ISSUES,EDIT_ISSUES,"
                                       "TRANSITION_ISSUES,ASSIGN_ISSUES,DELETE_ISSUES"})
        return {"me": me, "permissions": (perms or {}).get("permissions", {})}

    async def myself(self) -> dict:
        return await self._get("/rest/api/3/myself")

    # -- projects / metadata -------------------------------------------------
    async def list_projects(self) -> list[dict]:
        data = await self._get("/rest/api/3/project/search",
                               params={"expand": "description,lead", "maxResults": 100})
        projects = (data or {}).get("values", [])
        # Paginate if needed.
        start_at = len(projects)
        while data and not data.get("isLast", True):
            data = await self._get("/rest/api/3/project/search",
                                   params={"expand": "description,lead",
                                           "maxResults": 100, "startAt": start_at})
            projects.extend((data or {}).get("values", []))
            start_at = len(projects)
        return projects

    async def create_meta(self, project_keys: list[str] | None = None) -> dict:
        params: dict = {"expand": "projects.issuetypes.fields"}
        if project_keys:
            params["projectKeys"] = ",".join(project_keys)
        return await self._get("/rest/api/3/issue/createmeta", params=params)

    async def edit_meta(self, issue_key: str) -> dict:
        return await self._get(f"/rest/api/3/issue/{issue_key}/editmeta")

    async def field_options(self) -> list[dict]:
        try:
            return await self._get("/rest/api/3/field") or []
        except JiraError:
            return []

    async def search_users(self, query: str, max_results: int = 10) -> list[dict]:
        return await self._get("/rest/api/3/user/search",
                               params={"query": query, "maxResults": max_results}) or []

    # -- search --------------------------------------------------------------
    def _search_paths(self) -> list[str]:
        if self._search_path:
            return [self._search_path]
        return ["/rest/api/3/search/jql", "/rest/api/3/search"]

    async def search(self, jql: str, *, fields: list[str] | None = None,
                     max_results: int = 50, next_token: str | None = None,
                     expand: list[str] | None = None) -> dict:
        """Paginated issue search. Returns raw envelope (issues + token/offset)."""
        last_err: JiraError | None = None
        for path in self._search_paths():
            is_new = path.endswith("/search/jql")
            payload: dict[str, Any] = {
                "jql": jql,
                "maxResults": max_results,
            }
            if fields:
                payload["fields"] = fields
            if expand:
                payload["expand"] = expand
            if is_new:
                if next_token:
                    payload["nextPageToken"] = next_token
            else:
                payload["startAt"] = int(next_token) if next_token else 0
            try:
                data = await self._request("POST", path, json=payload)
                self._search_path = path
                issues = (data or {}).get("issues", [])
                token = (data or {}).get("nextPageToken") if is_new else None
                if not is_new:
                    total = int((data or {}).get("total", 0))
                    start = int((data or {}).get("startAt", 0))
                    if start + len(issues) < total:
                        token = str(start + len(issues))
                return {"issues": issues, "nextPageToken": token,
                        "total": (data or {}).get("total"), "names": (data or {}).get("names", {})}
            except JiraError as exc:
                last_err = exc
                if exc.status in (404, 405, 410) and path.endswith("/search/jql"):
                    continue  # try legacy endpoint
                raise
        raise last_err or JiraError("server", "Issue search unavailable.")

    async def search_all(self, jql: str, *, fields: list[str] | None = None,
                         page_size: int = 100, limit: int = 5000) -> list[dict]:
        out: list[dict] = []
        token: str | None = None
        while True:
            page = await self.search(jql, fields=fields, max_results=page_size,
                                     next_token=token)
            out.extend(page["issues"])
            token = page.get("nextPageToken")
            if not token or len(out) >= limit:
                break
        return out[:limit]

    async def validate_jql(self, jql: str) -> dict:
        # Universal check: parse endpoint if present, else a 1-result search.
        try:
            await self._request("POST", "/rest/api/3/jql/parse",
                                json={"queries": [jql]})
            return {"valid": True}
        except JiraError as exc:
            if exc.status in (404, 405):
                try:
                    await self.search(jql, max_results=1)
                    return {"valid": True}
                except JiraError as exc2:
                    return {"valid": False, "error": str(exc2)[:500]}
            return {"valid": False, "error": str(exc)[:500]}

    # -- issues ----------------------------------------------------------------
    async def get_issue(self, key: str, expand: str = "changelog,renderedFields") -> dict:
        return await self._get(f"/rest/api/3/issue/{key}",
                               params={"expand": expand})

    async def create_issue(self, fields: dict) -> dict:
        return await self._request("POST", "/rest/api/3/issue", json={"fields": fields})

    async def update_issue(self, key: str, fields: dict,
                           notify: bool = True) -> None:
        await self._request("PUT", f"/rest/api/3/issue/{key}",
                            params={"notifyUsers": str(notify).lower()},
                            json={"fields": fields})

    async def delete_issue(self, key: str, delete_subtasks: bool = False) -> None:
        await self._request("DELETE", f"/rest/api/3/issue/{key}",
                            params={"deleteSubtasks": str(delete_subtasks).lower()})

    async def transitions(self, key: str) -> list[dict]:
        data = await self._get(f"/rest/api/3/issue/{key}/transitions")
        return (data or {}).get("transitions", [])

    async def transition(self, key: str, transition_id: str,
                         fields: dict | None = None) -> None:
        body: dict = {"transition": {"id": str(transition_id)}}
        if fields:
            body["fields"] = fields
        await self._request("POST", f"/rest/api/3/issue/{key}/transitions", json=body)

    async def assign(self, key: str, account_id: str | None) -> None:
        body = {"accountId": account_id} if account_id else {"accountId": None}
        await self._request("PUT", f"/rest/api/3/issue/{key}/assignee", json=body)

    # -- comments ---------------------------------------------------------------
    async def comments(self, key: str, start_at: int = 0,
                       max_results: int = 50, order_by: str = "+created") -> dict:
        return await self._get(f"/rest/api/3/issue/{key}/comment",
                               params={"startAt": start_at, "maxResults": max_results,
                                       "orderBy": order_by, "expand": "renderedBody"})

    async def add_comment(self, key: str, body_adf: dict) -> dict:
        return await self._request("POST", f"/rest/api/3/issue/{key}/comment",
                                   json={"body": body_adf})

    async def update_comment(self, key: str, comment_id: str, body_adf: dict) -> dict:
        return await self._request("PUT", f"/rest/api/3/issue/{key}/comment/{comment_id}",
                                   json={"body": body_adf})

    async def delete_comment(self, key: str, comment_id: str) -> None:
        await self._request("DELETE", f"/rest/api/3/issue/{key}/comment/{comment_id}")

    # -- attachments --------------------------------------------------------------
    async def attachment_meta(self, key: str) -> list[dict]:
        issue = await self.get_issue(key)
        return ((issue.get("fields") or {}).get("attachment") or [])

    async def download_attachment(self, url: str) -> tuple[bytes, str]:
        """Download via authenticated session. Returns (bytes, content_type)."""
        try:
            async with self._client() as c:
                # Attachment content URLs may be absolute; allow them.
                r = await c.get(url, headers={"Authorization": self._headers["Authorization"],
                                             "User-Agent": "atlas/1.0"})
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise JiraError("network", f"Attachment download failed: {exc}")
        if r.status_code >= 400:
            raise JiraError(self._classify(r.status_code, r.text[:500]),
                            f"Attachment HTTP {r.status_code}", r.status_code)
        return bytes(r.content), r.headers.get("content-type", "application/octet-stream")

    async def upload_attachment(self, key: str, filename: str, data: bytes,
                                content_type: str = "application/octet-stream") -> list[dict]:
        headers = {"X-Atlassian-Token": "no-check"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers,
                                         timeout=self.timeout, transport=self._transport,
                                         follow_redirects=True) as c:
                r = await c.post(f"/rest/api/3/issue/{key}/attachments",
                                 files={"file": (filename, data, content_type)},
                                 headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise JiraError("network", f"Attachment upload failed: {exc}")
        if r.status_code >= 400:
            raise JiraError(self._classify(r.status_code, r.text[:500]),
                            f"Attachment upload HTTP {r.status_code}: {r.text[:500]}",
                            r.status_code)
        return r.json()

    async def delete_attachment(self, attachment_id: str) -> None:
        await self._request("DELETE", f"/rest/api/3/attachment/{attachment_id}")

    # -- links / watchers / votes / worklogs ----------------------------------------
    async def link_types(self) -> dict:
        return await self._get("/rest/api/3/issueLinkType")

    async def create_link(self, link_type: str, inward_key: str,
                          outward_key: str, comment: str = "") -> None:
        body: dict = {"type": {"name": link_type},
                      "inwardIssue": {"key": inward_key},
                      "outwardIssue": {"key": outward_key}}
        if comment:
            from .adf import markdown_to_adf
            body["comment"] = {"body": markdown_to_adf(comment)}
        await self._request("POST", "/rest/api/3/issueLink", json=body)

    async def delete_link(self, link_id: str) -> None:
        await self._request("DELETE", f"/rest/api/3/issueLink/{link_id}")

    async def worklogs(self, key: str) -> list[dict]:
        data = await self._get(f"/rest/api/3/issue/{key}/worklog")
        return (data or {}).get("worklogs", [])

    async def add_worklog(self, key: str, time_spent: str, comment: str = "",
                          started: str | None = None) -> dict:
        from .adf import markdown_to_adf
        body: dict = {"timeSpent": time_spent}
        if comment:
            body["comment"] = markdown_to_adf(comment)
        if started:
            body["started"] = started
        return await self._request("POST", f"/rest/api/3/issue/{key}/worklog", json=body)

    async def add_watcher(self, key: str, account_id: str) -> None:
        await self._request("POST", f"/rest/api/3/issue/{key}/watchers",
                            json=account_id,
                            headers={"Content-Type": "application/json"})

    # -- agile -----------------------------------------------------------------------
    async def _agile(self, method: str, path: str, *,
                     params: dict | None = None, json: Any = None) -> Any:
        try:
            async with httpx.AsyncClient(
                    base_url=self.base_url, headers=self._agile_headers,
                    timeout=self.timeout, transport=self._transport,
                    follow_redirects=True) as c:
                r = await c.request(method, path, params=params, json=json)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise JiraError("network", f"Agile API unreachable: {exc}")
        if r.status_code >= 400:
            raise JiraError(self._classify(r.status_code, r.text[:500]),
                            f"Agile HTTP {r.status_code}: {r.text[:500]}",
                            r.status_code)
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    async def boards(self, project_key: str | None = None,
                     board_type: str | None = None) -> list[dict]:
        params: dict = {"maxResults": 50}
        if project_key:
            params["projectKeyOrId"] = project_key
        if board_type:
            params["type"] = board_type
        out: list[dict] = []
        start_at = 0
        while True:
            data = await self._agile("GET", "/rest/agile/1.0/board",
                                     params={**params, "startAt": start_at})
            vals = (data or {}).get("values", [])
            out.extend(vals)
            if (data or {}).get("isLast", True) or not vals:
                break
            start_at += len(vals)
        return out

    async def board(self, board_id: int) -> dict:
        return await self._agile("GET", f"/rest/agile/1.0/board/{board_id}")

    async def board_issues(self, board_id: int, jql: str = "",
                           max_results: int = 50, start_at: int = 0) -> dict:
        params: dict = {"maxResults": max_results, "startAt": start_at}
        if jql:
            params["jql"] = jql
        return await self._agile("GET", f"/rest/agile/1.0/board/{board_id}/issue",
                                 params=params)

    async def board_config(self, board_id: int) -> dict:
        return await self._agile("GET", f"/rest/agile/1.0/board/{board_id}/configuration")

    async def sprints(self, board_id: int, state: str | None = None) -> list[dict]:
        params: dict = {"maxResults": 50}
        if state:
            params["state"] = state
        out: list[dict] = []
        start_at = 0
        while True:
            data = await self._agile("GET", f"/rest/agile/1.0/board/{board_id}/sprint",
                                     params={**params, "startAt": start_at})
            vals = (data or {}).get("values", [])
            out.extend(vals)
            if (data or {}).get("isLast", True) or not vals:
                break
            start_at += len(vals)
        return out

    async def sprint_issues(self, sprint_id: int, max_results: int = 50,
                            start_at: int = 0, jql: str = "") -> dict:
        params: dict = {"maxResults": max_results, "startAt": start_at}
        if jql:
            params["jql"] = jql
        return await self._agile("GET", f"/rest/agile/1.0/sprint/{sprint_id}/issue",
                                 params=params)

    async def rank_issues(self, issue_keys: list[str], *,
                          rank_before: str | None = None,
                          rank_after: str | None = None,
                          rank_custom_field_id: int | None = None) -> None:
        body: dict = {"issues": issue_keys}
        if rank_before:
            body["rankBeforeIssue"] = rank_before
        elif rank_after:
            body["rankAfterIssue"] = rank_after
        else:
            raise JiraError("validation",
                            "rank requires rankBeforeIssue or rankAfterIssue.")
        if rank_custom_field_id:
            body["rankCustomFieldId"] = rank_custom_field_id
        await self._agile("PUT", "/rest/agile/1.0/issue/rank", json=body)

    async def filters(self) -> list[dict]:
        try:
            data = await self._get("/rest/api/3/filter/search",
                                   params={"maxResults": 50})
            return (data or {}).get("values", [])
        except JiraError:
            return []

    async def close(self) -> None:
        return None
