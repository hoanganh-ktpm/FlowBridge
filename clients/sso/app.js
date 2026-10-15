// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import Keycloak from "/keycloak.js";

const config = await (await fetch("/config.json")).json();
const realmMarker = config.issuer.lastIndexOf("/realms/");
const keycloak = new Keycloak({
  url: config.issuer.slice(0, realmMarker),
  realm: config.issuer.slice(realmMarker + 8),
  clientId: config.clientId,
});
const element = (id) => document.getElementById(id);
element("client").textContent = `Ứng dụng ${config.client}`;
function render() {
  element("status").textContent = keycloak.authenticated ? "Đã đăng nhập" : "Chưa đăng nhập";
  element("login").disabled = !!keycloak.authenticated;
  for (const id of ["profile", "refresh", "copy", "logout"]) {
    element(id).disabled = !keycloak.authenticated;
  }
}
async function action(work) {
  try { await work(); }
  catch { element("result").textContent = "Thao tác thất bại. Kiểm tra phiên đăng nhập và cấu hình dịch vụ."; }
  render();
}
await action(async () => {
  await keycloak.init({pkceMethod: "S256", checkLoginIframe: false});
});
element("login").onclick = () => action(() => keycloak.login({redirectUri: location.origin + "/"}));
element("logout").onclick = () => action(() => keycloak.logout({redirectUri: location.origin + "/"}));
element("refresh").onclick = () => action(async () => {
  await keycloak.updateToken(-1);
  element("result").textContent = "Phiên đã được làm mới.";
});
element("profile").onclick = () => action(async () => {
  await keycloak.updateToken(30);
  const response = await fetch(config.apiUrl + "/api/v1/me", {
    headers: {Authorization: "Bearer " + keycloak.token},
  });
  element("result").textContent = JSON.stringify({status: response.status, body: await response.json()}, null, 2);
});
element("copy").onclick = () => action(async () => {
  await keycloak.updateToken(30);
  await navigator.clipboard.writeText(keycloak.token);
  element("result").textContent = "Đã sao chép access token.";
});
