"""Putting a file in the vault, and the ways it may not be done."""

import hashlib
import shutil
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.documents.models import Document
from apps.lots.models import Lot
from apps.registry.models import (
    Farm,
    Membership,
    OrganisationType,
    Party,
    Product,
    Role,
    User,
)

# A one-pixel PNG. Small enough to inline, real enough to be a file.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class UploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        party = Party.objects.create(
            code="ORG-H1",
            legal_name="Samarqand Hub",
            type=OrganisationType.objects.get(code="operator"),
            verification_status="verified",
        )
        cls.user = User.objects.create(
            username="q.inspector", display_name="Q. Inspector",
            status="active", oneid_verified=True,
        )
        Membership.objects.create(
            user=cls.user, party=party, role=Role.objects.get(code="qc_inspector")
        )
        product = Product.objects.create(
            code="melon", name_uz="Qovun", name_ru="Дыня", name_en="Melon"
        )
        farm = Farm.objects.create(code="F-1", party=party, name="Farm")
        cls.lot = Lot.objects.create(
            code="AZ-2026-TEST-0001",
            product=product,
            origin_farm=farm,
            owner_party=party,
            net_weight_g=1_000_000,
            gross_weight_g=1_050_000,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.access = self.client.post(
            reverse("registry:oneid"),
            {"persona": "q.inspector"},
            content_type="application/json",
        ).json()["access"]

    def upload(self, content=PIXEL, name="bench.png", content_type="image/png", **extra):
        return self.client.post(
            reverse("documents:upload"),
            {
                "file": SimpleUploadedFile(name, content, content_type=content_type),
                "subject_type": "lot",
                "subject_code": self.lot.code,
                "doc_type": "photo",
                **extra,
            },
            headers={"authorization": f"Bearer {self.access}"},
        )

    def test_a_photograph_is_stored_and_the_row_keeps_the_key(self):
        response = self.upload()

        self.assertEqual(response.status_code, 201, response.content[:300])
        document = Document.objects.get()
        self.assertTrue(document.file_ref.startswith("documents/lot/"))
        self.assertTrue(default_storage.exists(document.file_ref))
        self.assertEqual(document.byte_size, len(PIXEL))
        self.assertEqual(document.status, Document.Status.ISSUED)

    def test_the_checksum_is_of_what_was_written(self):
        """Evidence shown months later needs something to be shown against."""
        self.upload()

        document = Document.objects.get()
        with default_storage.open(document.file_ref) as stored:
            self.assertEqual(
                hashlib.sha256(stored.read()).hexdigest(), document.checksum_sha256
            )

    def test_the_stored_name_comes_from_here_not_from_the_caller(self):
        """A name from a form is a way into somebody else's directory."""
        self.upload(name="../../../../etc/passwd.png")

        document = Document.objects.get()
        self.assertNotIn("..", document.file_ref)
        self.assertTrue(document.file_ref.startswith("documents/lot/"))

    def test_it_lands_in_the_lot_s_own_log(self):
        self.upload()

        event = self.lot.events.order_by("-sequence").first()
        self.assertEqual(event.event_type, "document_issued")
        self.assertEqual(event.payload["type"], "photo")

    def test_an_executable_is_not_a_document(self):
        response = self.upload(
            content=b"#!/bin/sh\necho hi\n",
            name="run.sh",
            content_type="application/x-sh",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Document.objects.exists())

    def test_a_file_too_large_is_refused_before_it_is_written(self):
        response = self.upload(content=b"\0" * (8 * 1024 * 1024 + 1))

        self.assertEqual(response.status_code, 400)
        self.assertIn("8 MB", response.json()["blockers"][0])
        self.assertFalse(Document.objects.exists())

    def test_nothing_attached_is_said_plainly(self):
        response = self.client.post(
            reverse("documents:upload"),
            {"subject_type": "lot", "subject_code": self.lot.code, "doc_type": "photo"},
            headers={"authorization": f"Bearer {self.access}"},
        )

        self.assertEqual(response.status_code, 400)

    def test_a_stranger_cannot_write_to_the_vault(self):
        response = self.client.post(
            reverse("documents:upload"),
            {
                "file": SimpleUploadedFile("x.png", PIXEL, content_type="image/png"),
                "subject_type": "lot",
                "subject_code": self.lot.code,
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_a_lot_that_is_not_yours_cannot_be_given_evidence(self):
        """`capture` says they may record things; not whose."""
        other = Party.objects.create(
            code="ORG-X1",
            legal_name="Another farm",
            type=OrganisationType.objects.get(code="farmer"),
            verification_status="verified",
        )
        theirs = Lot.objects.create(
            code="AZ-2026-TEST-9999",
            product=Product.objects.first(),
            origin_farm=Farm.objects.create(code="F-9", party=other, name="Theirs"),
            owner_party=other,
            net_weight_g=1_000,
            gross_weight_g=1_100,
        )

        response = self.client.post(
            reverse("documents:upload"),
            {
                "file": SimpleUploadedFile("x.png", PIXEL, content_type="image/png"),
                "subject_type": "lot",
                "subject_code": theirs.code,
                "doc_type": "photo",
            },
            headers={"authorization": f"Bearer {self.access}"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Document.objects.filter(subject_code=theirs.code).exists())

    def test_the_url_is_absolute_so_a_separate_front_end_can_load_it(self):
        """In development the panels are a different origin from the API, so a
        relative /media path resolves against the wrong host."""
        body = self.upload().json()

        self.assertTrue(body["url"].startswith("http://"), body["url"])
        self.assertIn("/media/documents/lot/", body["url"])
