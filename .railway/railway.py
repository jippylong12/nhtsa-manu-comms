from railway_sdk import define_railway, image, project, service, volume


@define_railway
def main(ctx=None):
    pgvectorVolume = volume("pgvector-volume", { "alerts": { "usage": { "100": {}, "80": {}, "95": {} } }, "allowOnlineResize": True, "region": "us-west2", "sizeMB": 5000 })
    pgvector = service(
        "pgvector",
        source=image("pgvector/pgvector:pg18"),
        build={ "buildEnvironment": "V3", "builder": "RAILPACK" },
        start="/bin/sh -c \"unset PGPORT; docker-entrypoint.sh postgres --port=5432\"",
        replicas={ "us-west2": 1 },
        networking={ "tcpProxies": { "5432": {} } },
        volumeMounts={ "/var/lib/postgresql": pgvectorVolume },
    )
    api = service(
        "api",
        build={ "buildEnvironment": "V3", "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
        start="./start.sh",
        healthcheck="/api/health",
        replicas={ "us-west2": 1 },
        # Retry a crashed api up to five times. Restart-on-failure is Railway's default and it
        # stores the default as "unset", so naming it here would show as a change on every plan.
        deploy={ "restartPolicyMaxRetries": 5 },
        domains=["notjustmycar.com", "www.notjustmycar.com"],
    )
    # The nightly job shares the api image but runs the pipeline, not the web server, and has
    # no healthcheck: it starts on the cron, does its work and exits.
    nightly = service(
        "nightly",
        build={ "buildEnvironment": "V3", "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
        start="python -m src.jobs.run_pipeline --send-digest",
        replicas={ "us-west2": 1 },
        deploy={ "cronSchedule": "0 9 * * *", "restartPolicyType": "NEVER" },
    )
    return project("nhtsa-comms", variables={"managed": False}, resources=[pgvector, api, nightly, pgvectorVolume])
