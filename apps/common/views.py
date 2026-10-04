from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.common.readiness import dependency_status


@extend_schema(
    summary="Database and cache readiness",
    responses={200: dict, 503: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """No success status until every dependency needed to serve traffic works."""
    dependencies = dependency_status()
    response = Response(
        {
            "service": "agro-zanjir-digital",
            "version": "0.1.0",
            **dependencies,
        },
        status=200 if all(value == "ok" for value in dependencies.values()) else 503,
    )
    response["Cache-Control"] = "no-store"
    return response


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
            "admin": request.build_absolute_uri("/django-admin/"),
        }
    )
