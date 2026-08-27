"""Rule 6, as a test: clusters never reference each other.

A shipment does not know about a loan; a QC record does not know about a
buyer. Everything meets at the lot. That constraint is what lets three people
build six modules without constant merge conflicts, and it is the one rule
that a well-meaning foreign key can quietly destroy - which is why it is
checked here rather than left to code review.

What each cluster is allowed to point at:

    its own models          obviously
    apps.lots               the spine; everything meets there
    apps.registry           who and what: parties, farms, products, people
    apps.common             base models

Anything else is a violation. `apps.panels` is exempt and only it: it owns the
cross-cluster read composition the operator panels need, holds no tables of
its own, and is never imported by a cluster.
"""

from django.apps import apps
from django.test import SimpleTestCase

CLUSTERS = ["quality", "storage", "commercial", "finance", "documents"]
ALLOWED = {"lots", "registry", "common", "governance", "auth", "contenttypes"}


class ClusterIndependenceTests(SimpleTestCase):
    def test_no_cluster_references_another_cluster(self):
        violations = []

        for label in CLUSTERS:
            for model in apps.get_app_config(label).get_models():
                for field in model._meta.get_fields():
                    if not field.is_relation or not field.related_model:
                        continue
                    target = field.related_model._meta.app_label
                    if target in {label, *ALLOWED}:
                        continue
                    # A reverse accessor from another cluster is that cluster's
                    # doing, not this one's; only forward relations count.
                    if not getattr(field, "concrete", False):
                        continue
                    violations.append(
                        f"{label}.{model.__name__}.{field.name} -> {target}"
                    )

        self.assertEqual(
            violations,
            [],
            "These fields cross from one cluster to another. Hold the other "
            "side by code (as `Claim.excursion_code` and `Policy."
            "covered_shipment_code` do) instead of by foreign key.",
        )

    def test_the_spine_does_not_depend_on_any_cluster(self):
        """`lots` must be able to exist with every outer cluster uninstalled."""
        violations = []
        for model in apps.get_app_config("lots").get_models():
            for field in model._meta.get_fields():
                if not field.is_relation or not field.related_model:
                    continue
                if not getattr(field, "concrete", False):
                    continue
                target = field.related_model._meta.app_label
                if target not in {"lots", "registry", "common"}:
                    violations.append(f"lots.{model.__name__}.{field.name} -> {target}")

        self.assertEqual(violations, [])
