"""Direct messages and reports."""

from __future__ import annotations

from ..routing import AUTH_REQUIRED, route
from ..services import social as social_service
from .base import json_response, ok


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------


@route("GET", "/api/threads", auth=AUTH_REQUIRED)
def list_threads(request, ctx, auth, params):
    payload = social_service.list_threads(ctx, auth)
    payload["rules"] = (
        "Members can message creators they currently support, and creators can reply on their own "
        "page. When a membership period ends the conversation becomes read-only — nothing is deleted."
    )
    return ok(payload)


@route("POST", "/api/threads", auth=AUTH_REQUIRED)
def start_thread(request, ctx, auth, params):
    return json_response(social_service.start_thread(ctx, auth, request.json()), status=201)


@route("GET", "/api/threads/<int:thread_id>", auth=AUTH_REQUIRED)
def get_thread(request, ctx, auth, params):
    return ok(social_service.get_thread(ctx, auth, params["thread_id"]))


@route("POST", "/api/threads/<int:thread_id>/messages", auth=AUTH_REQUIRED)
def send_message(request, ctx, auth, params):
    return json_response(social_service.send_message(ctx, auth, params["thread_id"], request.json()),
                         status=201)


@route("POST", "/api/threads/<int:thread_id>/archive", auth=AUTH_REQUIRED)
def archive_thread(request, ctx, auth, params):
    payload = request.json()
    archived = payload.get("archived", True)
    return ok(social_service.archive_thread(ctx, auth, params["thread_id"], bool(archived)))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@route("GET", "/api/reports/mine", auth=AUTH_REQUIRED)
def my_reports(request, ctx, auth, params):
    return ok(social_service.my_reports(ctx, auth))


@route("POST", "/api/reports", auth=AUTH_REQUIRED)
def create_report(request, ctx, auth, params):
    return json_response(social_service.create_report(ctx, auth, request.json()), status=201)


@route("GET", "/api/reports/target", auth=AUTH_REQUIRED)
def report_target(request, ctx, auth, params):
    target_id = request.query_int("target_id", None)
    target_type = request.query_one("target_type") or "post"
    if not target_id:
        from ..http import bad_request

        raise bad_request("Choose what you are reporting.", code="validation_error", field="target_id")
    return ok(social_service.report_target_preview(ctx, auth, target_type, target_id))
