"""The seam from section 04 must hold: callers name a port, never an adapter."""

from django.test import SimpleTestCase, override_settings

from ports import get_port
from ports.base import PortResult
from ports.registry import ADAPTERS, UnknownAdapter, UnknownPort


class GetPortTests(SimpleTestCase):
    def test_every_port_resolves_to_its_manual_adapter(self):
        for port in ADAPTERS:
            with self.subTest(port=port):
                self.assertEqual(get_port(port).name, "manual")

    def test_all_five_ports_from_the_blueprint_exist(self):
        self.assertEqual(
            sorted(ADAPTERS), ["carrier", "customs", "insurer", "lender", "sensor"]
        )

    def test_unknown_port_is_rejected(self):
        with self.assertRaises(UnknownPort):
            get_port("warehouse")

    @override_settings(PORT_ADAPTERS={"lender": "iso20022"})
    def test_unconfigured_adapter_is_rejected(self):
        with self.assertRaises(UnknownAdapter):
            get_port("lender")


class ManualAdapterTests(SimpleTestCase):
    def test_manual_work_is_accepted_and_left_pending_for_an_operator(self):
        result = get_port("lender").submit_application("app-1", amount_minor=5_000_00)

        self.assertIsInstance(result, PortResult)
        # Accepted, not failed: the request is recorded, a human resolves it.
        self.assertTrue(result.accepted)
        self.assertEqual(result.state, "pending_operator")
        self.assertEqual(result.payload["application_id"], "app-1")

    def test_sensor_batches_report_the_row_count_they_recorded(self):
        result = get_port("sensor").ingest(
            "storage_zone",
            "zone-1",
            ({"sensor_id": "s1", "ts": "2026-08-26T00:00:00Z"} for _ in range(3)),
        )

        self.assertEqual(result.payload["row_count"], 3)
