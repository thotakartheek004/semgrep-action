import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import NoReturn
from typing import Optional

import click
import sh
from boltons import ecoutils
from boltons.strutils import unit_len

from semgrep_agent import formatter
from semgrep_agent import semgrep
from semgrep_agent.meta import detect_meta_environment
from semgrep_agent.meta import GitMeta
from semgrep_agent.semgrep_app import Sapp
from semgrep_agent.utils import maybe_print_debug_info


def url(string: str) -> str:
    return string.rstrip("/")


@dataclass
class CliObj:
    event_type: str
    config: str
    meta: GitMeta
    sapp: Sapp


def get_event_type() -> str:
    if "GITHUB_ACTIONS" in os.environ:
        return os.environ["GITHUB_EVENT_NAME"]

    return "push"


@click.command()
@click.option("--config", envvar="INPUT_CONFIG", type=str)
@click.option(
    "--baseline-ref",
    envvar="BASELINE_REF",
    type=str,
    default=None,
    show_default="detected from CI env",
)
@click.option(
    "--publish-url", envvar="INPUT_PUBLISHURL", type=url, default="https://semgrep.dev"
)
@click.option("--publish-token", envvar="INPUT_PUBLISHTOKEN", type=str)
@click.option("--publish-deployment", envvar="INPUT_PUBLISHDEPLOYMENT", type=int)
@click.option("--json", "json_output", hidden=True, is_flag=True)
@click.pass_context
def main(
    ctx: click.Context,
    config: str,
    baseline_ref: str,
    publish_url: str,
    publish_token: str,
    publish_deployment: int,
    json_output: bool,
) -> NoReturn:
    click.echo("=== detecting environment", err=True)
    click.echo(
        f"| versions    - "
        f"semgrep {sh.semgrep(version=True).strip()} on "
        f"{sh.python(version=True).strip()}",
        err=True,
    )

    Meta = detect_meta_environment()
    meta_kwargs = {}
    if baseline_ref:
        meta_kwargs["cli_baseline_ref"] = baseline_ref

    obj = ctx.obj = CliObj(
        event_type=get_event_type(),
        config=config,
        meta=Meta(ctx, **meta_kwargs),
        sapp=Sapp(
            ctx=ctx,
            url=publish_url,
            token=publish_token,
            deployment_id=publish_deployment,
        ),
    )
    click.echo(
        f"| environment - "
        f"running in {obj.meta.environment}, "
        f"triggering event is '{obj.meta.event_name}'",
        err=True,
    )

    if obj.sapp.is_configured:
        click.echo(
            f"| semgrep.dev - logged in as deployment #{obj.sapp.deployment_id}",
            err=True,
        )
    else:
        click.echo("| semgrep.dev - not logged in", err=True)

    maybe_print_debug_info(obj.meta)

    obj.sapp.report_start()

    click.echo("=== setting up agent configuration", err=True)
    if obj.config:
        click.echo(f"| using semgrep rules from {obj.config}", err=True)
    elif obj.sapp.is_configured:
        click.echo("| using semgrep rules configured on the web UI", err=True)
    elif Path(".semgrep.yml").is_file():
        click.echo("| using semgrep rules from the committed .semgrep.yml", err=True)
    else:
        message = """
            == [ERROR] you didn't configure what rules semgrep should scan for.

            Please either set a config in the CI configuration according to
            https://github.com/returntocorp/semgrep-action#configuration
            or commit your own rules at the default path of `.semgrep.yml`
        """
        message = dedent(message).strip()
        click.echo(message, err=True)
        sys.exit(1)

    results = semgrep.scan(ctx)
    new_findings = results.findings.new

    blocking_findings = {finding for finding in new_findings if finding.is_blocking()}

    if json_output:
        # Output all new findings as json
        output = [f.to_dict() for f in new_findings]
        click.echo(json.dumps(output))
    else:
        # Print out blocking findings
        formatter.dump(blocking_findings)

    non_blocking_findings = {
        finding for finding in new_findings if not finding.is_blocking()
    }
    if non_blocking_findings:
        click.echo(
            f"| {unit_len(non_blocking_findings, 'non-blocking finding')} hidden in output",
            err=True,
        )

    obj.sapp.report_results(results)

    exit_code = 1 if blocking_findings else 0
    click.echo(
        f"=== exiting with {'failing' if exit_code == 1 else 'success'} status",
        err=True,
    )
    sys.exit(exit_code)
