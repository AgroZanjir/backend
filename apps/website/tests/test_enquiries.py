"""The contact form, which is the website's only write."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.throttling import SimpleRateThrottle

from apps.website.models import Enquiry

BODY = {
    "name": "N. Sharipov",
    "organisation": "Nodir dehqon xo'jaligi",
    "email": "nodir@example.uz",
    "phone": "+998 90 123 45 67",
    "topic": "w_ct_t1",
    "message": "We farm 40 hectares of melon and would like to join the pilot.",
}


class EnquiryTests(TestCase):
    def setUp(self):
        # The throttle counters live in the cache and would otherwise leak from
        # one test into the next.
        cache.clear()

    def post(self, **overrides):
        return self.client.post(
            reverse("website:enquire"),
            {**BODY, **overrides},
            content_type="application/json",
        )

    def test_a_stranger_can_send_one_without_a_session(self):
        response = self.post()

        self.assertEqual(response.status_code, 201, response.content[:200])
        self.assertEqual(response.json(), {"received": True, "topic": "w_ct_t1"})
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.name, "N. Sharipov")
        self.assertEqual(enquiry.status, Enquiry.Status.NEW)

    def test_the_reply_carries_nothing_to_guess_at(self):
        """A stranger is told it arrived, not given a handle on the record."""
        body = self.post().json()

        self.assertNotIn("id", body)

    def test_an_address_is_required(self):
        response = self.post(email="")

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json())
        self.assertFalse(Enquiry.objects.exists())

    def test_two_words_is_not_an_enquiry(self):
        response = self.post(message="call me")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Enquiry.objects.exists())

    def test_an_unknown_topic_is_refused(self):
        response = self.post(topic="w_ct_t9")

        self.assertEqual(response.status_code, 400)

    def test_one_address_cannot_fill_the_table(self):
        """`THROTTLE_RATES` is read off the class, so overriding the setting
        after import does nothing - the rate has to be patched where the
        throttle actually looks."""
        with patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"enquiry-burst": "2/min"}):
            self.assertEqual(self.post().status_code, 201)
            self.assertEqual(self.post().status_code, 201)

            self.assertEqual(self.post().status_code, 429)

        self.assertEqual(Enquiry.objects.count(), 2)

    def test_the_sender_address_is_kept(self):
        self.client.post(
            reverse("website:enquire"),
            BODY,
            content_type="application/json",
            headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
        )

        self.assertEqual(Enquiry.objects.get().source_address, "203.0.113.9")
