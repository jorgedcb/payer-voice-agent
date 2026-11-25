"""Which benefit topics the verification prompt asks about, offline.

The topics are the prompt's numbered `### N. Title` headings; the backend's
extraction contract reads the same headings.
"""

import re
from itertools import product

from dispatch import sample_spec
from prompts import instructions

_TOPIC = re.compile(r"^### \d+\. (.+)$", re.M)


def titles(**overrides: object) -> list[str]:
    return _TOPIC.findall(instructions("verification", sample_spec(**overrides)))


def test_topics_follow_what_the_call_asks() -> None:
    assert titles() == [
        "Benefits for physical therapy",
        "Provider network status",
        "Coverage effective date",
        "Coverage expiration date",
        "Benefit year type and benefit period end date",
        "Funding",
        "Coordination of benefits",
        "Out-of-state coverage",
        "Prior authorization",
        "Visit limits",
        "Visit limit type and visits used",
        "Out-of-pocket maximum applies to the service",
        "Copay counts toward the out-of-pocket maximum",
        "Deductible counts toward the out-of-pocket maximum",
        "Medical-necessity review",
        "Out-of-network reimbursement",
    ]
    # The provider's network comes first when only that network is collected.
    assert titles(network_benefit_scope="provider_network")[:2] == [
        "Provider network status",
        "Benefits for physical therapy",
    ]
    # Topics the spec settles or leaves out are not asked about.
    left_out = set(titles()) - set(
        titles(
            auth_required_in_network=True,
            auth_required_out_network=True,
            ask_visit_limits=False,
            ask_mnr=False,
        )
    )
    assert left_out == {
        "Prior authorization",
        "Visit limits",
        "Visit limit type and visits used",
        "Medical-necessity review",
    }


def test_every_spec_renders_distinct_topics() -> None:
    # The entrypoint renders the prompt before dialing; every spec branch must
    # render, each topic under its own title.
    services = ["physical_therapy", "speech_therapy", "mental_health"]
    auths = [None, True]
    for scope, contract, service, dx, auth_in, auth_out, limits, mnr in product(
        ["both_networks", "provider_network"],
        ["in_network", "out_of_network", None],
        services,
        ["", "F84.0"],
        auths,
        auths,
        [True, False],
        [True, False],
    ):
        spec = sample_spec(
            network_benefit_scope=scope,
            contract_network=contract,
            service_type=service,
            dx_code=dx,
            auth_required_in_network=auth_in,
            auth_required_out_network=auth_out,
            ask_visit_limits=limits,
            ask_mnr=mnr,
        )
        prompt = instructions("verification", spec)
        rendered = _TOPIC.findall(prompt)
        # Every heading the template rendered is a numbered topic, each its own.
        assert rendered and len(rendered) == prompt.count("\n### ")
        assert len(set(rendered)) == len(rendered)
