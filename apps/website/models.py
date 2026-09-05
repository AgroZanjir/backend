"""What the public website collects.

One table. The website is otherwise a reader - it shows a lot passport and
answers questions - and this is the single thing a visitor can put into the
platform. It is deliberately not a cluster: nothing in the value chain depends
on it, and nothing here may be trusted as identity. An enquiry is somebody
typing into a box on the open internet.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import BaseModel


class Enquiry(BaseModel):
    """A message from the contact form."""

    class Topic(models.TextChoices):
        # Kept as the website's own keys so the label file stays the one place
        # the four topics are written, in three languages.
        PILOT = "w_ct_t1", "Joining the pilot"
        PARTNER = "w_ct_t2", "Partnership"
        TECHNICAL = "w_ct_t3", "Technical"
        OTHER = "w_ct_t4", "Other"

    class Status(models.TextChoices):
        NEW = "new", "New"
        ANSWERED = "answered", "Answered"
        SPAM = "spam", "Spam"

    name = models.CharField(max_length=120)
    organisation = models.CharField(max_length=160, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    topic = models.CharField(max_length=16, choices=Topic, default=Topic.OTHER)
    message = models.TextField(max_length=4000)
    status = models.CharField(max_length=16, choices=Status, default=Status.NEW)
    # Kept for the same reason a mail server keeps one: to recognise the sender
    # again when the same address sends the hundredth message this hour.
    source_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"
