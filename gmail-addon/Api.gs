/**
 * Thin UrlFetchApp wrapper matching api/README.md's documented contract
 * exactly — same endpoints, same camelCase wire format
 * (api/serializers.py) that the Valence web UI already consumes. No new
 * server-side surface needed for the add-on.
 */

function apiGetEmail_(emailId) {
  var response = UrlFetchApp.fetch(
    apiBaseUrl_() + '/api/emails/' + encodeURIComponent(emailId),
    { method: 'get', headers: authHeaders_(), muteHttpExceptions: true }
  );
  if (response.getResponseCode() === 404) return null;
  return parseOrThrow_(response);
}

function apiSaveOutline_(emailId, bullets) {
  var response = UrlFetchApp.fetch(
    apiBaseUrl_() + '/api/emails/' + encodeURIComponent(emailId) + '/outline',
    {
      method: 'patch',
      contentType: 'application/json',
      headers: authHeaders_(),
      payload: JSON.stringify({ outline: bullets }),
      muteHttpExceptions: true,
    }
  );
  return parseOrThrow_(response);
}

function apiExpandDraft_(emailId) {
  var response = UrlFetchApp.fetch(
    apiBaseUrl_() + '/api/emails/' + encodeURIComponent(emailId) + '/expand',
    { method: 'post', headers: authHeaders_(), muteHttpExceptions: true }
  );
  // 501 = drafting/expand.py not implemented yet, same as the web UI sees.
  if (response.getResponseCode() === 501) return { notImplemented: true };
  return parseOrThrow_(response);
}

function parseOrThrow_(response) {
  var code = response.getResponseCode();
  if (code < 200 || code >= 300) {
    var detail = response.getContentText();
    try {
      detail = JSON.parse(detail).detail || detail;
    } catch (e) {
      // not JSON — use the raw body
    }
    throw new Error(code + ': ' + detail);
  }
  return JSON.parse(response.getContentText());
}
