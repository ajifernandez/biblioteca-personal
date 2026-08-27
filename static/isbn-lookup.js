async function lookupIsbnProgressive(isbn, options) {
  options = options || {};
  var onStatus = options.onStatus || function () {};
  var excludeBookId = options.excludeBookId;

  var providers = [];
  try {
    var providersRes = await fetch("/api/lookup-providers");
    providers = await providersRes.json();
  } catch (e) {
    providers = [];
  }

  var info = null;
  for (var i = 0; i < providers.length; i++) {
    var provider = providers[i];
    onStatus("Buscando en " + provider.label + "...");
    var url = "/api/lookup/" + provider.id + "/" + isbn;
    if (excludeBookId) url += "?exclude_book_id=" + excludeBookId;
    var data;
    try {
      var res = await fetch(url);
      data = await res.json();
    } catch (e) {
      continue;
    }
    if (data.found) {
      if (!info) {
        info = data;
      } else if (!info.cover_url && data.cover_url) {
        info.cover_url = data.cover_url;
      }
      if (info.cover_url) break;
    }
  }
  return info;
}

async function fetchIsbnSearchLinks(isbn) {
  try {
    var res = await fetch("/api/isbn-search-links/" + isbn);
    return await res.json();
  } catch (e) {
    return [];
  }
}
