/** Navbatchi control bot — Railway deploy + env */

const TOKEN = (process.env.RAILWAY_TOKEN || "").trim();
if (!TOKEN) {
  console.error("RAILWAY_TOKEN yo'q");
  process.exit(1);
}

const API = "https://backboard.railway.com/graphql/v2";
const DEFAULT_HUB_URL = "https://davlat-yordamchi-bot-production.up.railway.app";

const FACE = {
  projectId: "5034d01f-656a-4fa0-b9c3-400cb702a992",
  environmentId: "bad3f0da-ce42-4eb0-a580-cb5f929d548e",
  serviceId: "4959b353-7e83-4d69-be87-b30b05dc706e",
};

async function gql(query, variables = {}) {
  const res = await fetch(API, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, variables }),
  });
  const data = await res.json();
  if (data.errors?.length) {
    throw new Error(data.errors.map((e) => e.message).join("; "));
  }
  return data.data;
}

async function getVariables({ projectId, environmentId, serviceId }) {
  const q = `query($projectId: String!, $environmentId: String!, $serviceId: String!) {
    variables(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId)
  }`;
  const data = await gql(q, { projectId, environmentId, serviceId });
  return data.variables || {};
}

async function upsertVariables({ projectId, environmentId, serviceId, variables }) {
  const q = `mutation($input: VariableCollectionUpsertInput!) {
    variableCollectionUpsert(input: $input)
  }`;
  await gql(q, {
    input: { projectId, environmentId, serviceId, variables, replace: false },
  });
}

async function findNavbatchiService() {
  const data = await gql(`query {
    projects { edges { node {
      id
      name
      environments { edges { node {
        id
        serviceInstances { edges { node { serviceId serviceName } } }
      } } }
    } } }
  }`);

  for (const pe of data.projects.edges) {
    for (const ee of pe.node.environments.edges) {
      for (const sie of ee.node.serviceInstances.edges) {
        const si = sie.node;
        const name = (si.serviceName || "").toLowerCase();
        if (name.includes("navbatchi")) {
          return {
            projectId: pe.node.id,
            environmentId: ee.node.id,
            serviceId: si.serviceId,
            serviceName: si.serviceName,
          };
        }
      }
    }
  }
  return null;
}

async function main() {
  const nav = await findNavbatchiService();
  if (!nav) {
    console.error("Navbatchi servisi topilmadi");
    process.exit(1);
  }
  console.log("service:", nav.serviceName, nav.serviceId);

  const faceVars = await getVariables(FACE);
  const hubSecret = (
    process.env.YORDAMCHI_HUB_SECRET ||
    faceVars.YORDAMCHI_HUB_SECRET ||
    ""
  ).trim();
  const hubUrl = (
    process.env.YORDAMCHI_HUB_URL ||
    faceVars.YORDAMCHI_HUB_URL ||
    DEFAULT_HUB_URL
  ).trim();

  await upsertVariables({
    projectId: nav.projectId,
    environmentId: nav.environmentId,
    serviceId: nav.serviceId,
    variables: {
      GROUP_CHAT_ID: "-1001877019294",
      EXTRA_GROUP_IDS: "-5351426801",
      DATABASE_DIR: "/data",
      ADMIN_IDS: "1432810519",
      TZ: "Asia/Tashkent",
      MORNING_HOUR: "7",
      MORNING_MINUTE: "30",
      EVENING_HOUR: "20",
      EVENING_MINUTE: "0",
      ...(hubUrl ? { YORDAMCHI_HUB_URL: hubUrl } : {}),
      ...(hubSecret ? { YORDAMCHI_HUB_SECRET: hubSecret } : {}),
    },
  });
  console.log("env:", hubSecret ? "hub OK" : "hub secret yo'q");

  const r = await gql(
    `mutation($s:String!,$e:String!){ serviceInstanceDeploy(serviceId:$s,environmentId:$e,latestCommit:true) }`,
    { s: nav.serviceId, e: nav.environmentId }
  );
  console.log("deploy navbatchi:", r.serviceInstanceDeploy ? "OK" : "?");
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
