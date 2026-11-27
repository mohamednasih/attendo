const DEFAULT_PORT = 47831;
const loopbackHosts = new Set(['localhost', '127.0.0.1', '[::1]']);

export const isHosted = !loopbackHosts.has(window.location.hostname);

const portParam = new URLSearchParams(window.location.search).get('port');
const port = /^[0-9]{1,5}$/.test(portParam) && Number(portParam) > 0 && Number(portParam) <= 65535
  ? Number(portParam)
  : DEFAULT_PORT;

export function apiUrl(path) {
  return isHosted ? `http://127.0.0.1:${port}${path}` : path;
}

export function apiFetch(path, options = {}) {
  const requestOptions = isHosted
    ? {...options, mode: 'cors', targetAddressSpace: 'loopback'}
    : options;
  return fetch(apiUrl(path), requestOptions);
}
