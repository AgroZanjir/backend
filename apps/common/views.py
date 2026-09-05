from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@extend_schema(
    summary="Liveness and database check",
    responses={200: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Cheap check that the process is up and the database answers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database = "ok"
    except Exception as exc:  # pragma: no cover - surfaced verbatim to the caller
        database = f"error: {exc}"

    return Response(
        {
            "service": "agro-zanjir-digital",
            "version": "0.1.0",
            "database": database,
            "engine": connection.vendor,
        }
    )


@extend_schema(
    summary="Where the entry points are",
    responses={200: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def index(request):
    """A signpost at the root of the API host.

    Nothing is served here - the website is a separate origin, and this
    process only answers under /api/v1/. Without this an operator who opens
    the API host in a browser gets Django's 404, which reads as a broken
    deployment when the deployment is fine. So say what is here instead.
    """
    return Response(
        {
            "service": "agro-zanjir-digital",
            "api": request.build_absolute_uri("/api/v1/"),
            "health": request.build_absolute_uri("/api/v1/health/"),
            "docs": request.build_absolute_uri("/api/docs/"),
            "schema": request.build_absolute_uri("/api/schema/"),
            "admin": request.build_absolute_uri("/admin/"),
        }
    )
