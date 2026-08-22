/**
 * Script properties are set once via the Apps Script editor (Project
 * Settings > Script Properties) — never committed to source. See
 * gmail-addon/README.md for setup.
 */

function apiBaseUrl_() {
  var url = PropertiesService.getScriptProperties().getProperty('API_BASE_URL');
  if (!url) throw new Error('Script property API_BASE_URL is not set.');
  return url.replace(/\/$/, '');
}

function authHeaders_() {
  var token = PropertiesService.getScriptProperties().getProperty('API_TOKEN');
  return token ? { Authorization: 'Bearer ' + token } : {};
}
