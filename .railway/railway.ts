import { defineRailway, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const chartedNewsEngineVolume = volume("charted-news-engine-volume", {
    alerts: { usage: { "100": {}, "80": {}, "95": {} } },
    allowOnlineResize: true,
    region: "iad",
    sizeMB: 500,
  });
  const chartedNewsEngine = service("charted-news-engine", {
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "python -m cne.deploy",
    healthcheck: "/api/health",
    healthcheckTimeout: 300,
    replicas: { iad: 1 },
    volumeMounts: { "/data": chartedNewsEngineVolume },
  });

  return project("charted-news-engine", {
    variables: { managed: false },
    resources: [chartedNewsEngine, chartedNewsEngineVolume],
  });
});
