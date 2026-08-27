from django.test import TestCase


class HealthTests(TestCase):
    def test_health_is_open_and_reports_the_database(self):
        response = self.client.get("/api/v1/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "ok")
