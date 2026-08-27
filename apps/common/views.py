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
