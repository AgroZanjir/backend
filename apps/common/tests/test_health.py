from django.test import TestCase


class HealthTests(TestCase):
    def test_health_is_open_and_reports_the_database(self):
        response = self.client.get("/api/v1/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "ok")


class IndexTests(TestCase):
    def test_the_root_points_at_the_entry_points_instead_of_404ing(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["health"].endswith("/api/v1/health/"))
        self.assertTrue(body["docs"].endswith("/api/docs/"))
