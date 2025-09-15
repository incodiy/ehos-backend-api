import asyncio

import click


@click.command()
def seed() -> None:
    """Run EHOS seeders (Phase 4c-4f: brands, hotels, tier, audit/CRM ingest, legacy)."""
    from app.seed.runner import run_seeders

    asyncio.run(run_seeders())
    click.echo("Seed complete.")


if __name__ == "__main__":
    seed()