"""The benefit topics an interview settles before it may close.

The verification prompt words each topic for the conversation; this is the same
list for code. The interview records a topic as the representative settles it,
and end_call will not start the closing while a topic the call needs is open:
on long calls the model alone lost its place and closed with topics unasked.
"""

import re
from typing import Literal, get_args

from dispatch import CallSpec

# In the order the prompt asks them, so the first open one is the next to ask.
Topic = Literal[
    "benefits",
    "provider_network_status",
    "coverage_effective_date",
    "coverage_expiration_date",
    "benefit_year_type",
    "funding",
    "coordination_of_benefits",
    "out_of_state_coverage",
    "prior_authorization",
    "visit_limits",
    "visit_limit_type_and_visits_used",
    "out_of_pocket_maximum_applies",
    "copay_counts_toward_out_of_pocket",
    "deductible_counts_toward_out_of_pocket",
    "medical_necessity_review",
    "out_of_network_reimbursement",
]
TOPICS: tuple[Topic, ...] = get_args(Topic)

PLAN_FACTS: frozenset[Topic] = frozenset(
    {
        "coverage_effective_date",
        "coverage_expiration_date",
        "benefit_year_type",
        "funding",
        "coordination_of_benefits",
        "out_of_state_coverage",
    }
)

_OUT_OF_NETWORK = re.compile(r"\bout[ -]of[ -]network\b", re.IGNORECASE)
_IN_NETWORK = re.compile(r"\bin[ -]network\b", re.IGNORECASE)


def provider_out_of_network(answer: str) -> bool:
    """Read a recorded network status; ValueError unless it says exactly one."""
    out, inside = bool(_OUT_OF_NETWORK.search(answer)), bool(_IN_NETWORK.search(answer))
    if out == inside:
        raise ValueError(answer)
    return out


def required_topics(
    spec: CallSpec, provider_out_of_network: bool | None
) -> list[Topic]:
    """The topics this call must settle, given the network status if known."""
    scope = spec.network_benefit_scope
    if scope == "in_network_only" and provider_out_of_network:
        # The prompt's own rule: an out-of-network provider gets the plan facts only.
        return [t for t in TOPICS if t == "provider_network_status" or t in PLAN_FACTS]

    needed: set[Topic] = set(TOPICS) - {
        "prior_authorization",
        "visit_limits",
        "visit_limit_type_and_visits_used",
        "medical_necessity_review",
        "out_of_network_reimbursement",
    }
    in_on_file = spec.auth_required_in_network is not None
    out_on_file = spec.auth_required_out_network is not None
    if scope == "both_networks" or provider_out_of_network is None:
        authorization_open = not (in_on_file and out_on_file)
    else:
        authorization_open = not (
            out_on_file if provider_out_of_network else in_on_file
        )
    if authorization_open:
        needed.add("prior_authorization")
    if spec.ask_visit_limits:
        needed |= {"visit_limits", "visit_limit_type_and_visits_used"}
    if spec.ask_mnr:
        needed.add("medical_necessity_review")
    if provider_out_of_network and scope != "in_network_only":
        needed.add("out_of_network_reimbursement")

    ordered = [t for t in TOPICS if t in needed]
    if scope != "both_networks":
        # The single-network scripts ask the network status first.
        ordered.remove("provider_network_status")
        ordered.insert(0, "provider_network_status")
    return ordered
