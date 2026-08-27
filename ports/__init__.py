"""Ports and adapters for every external relationship (section 04).

Bank, insurance and customs integrations are the reason projects like this slip
by a year. The work is not hard; the waiting is. So every external relationship
is a port - an interface the domain talks to - with a *manual adapter* behind
it first: the platform records that the interaction exists and what state it is
in, and a human moves that state from the admin screen after a phone call.
Later an API adapter moves the same state through the same port, and nothing
else in the system changes.

Five ports cover everything in the concept document.
"""

from ports.registry import get_port

__all__ = ["get_port"]
