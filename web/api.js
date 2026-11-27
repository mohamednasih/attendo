const LOOPBACK_ORIGIN = 'http://127.0.0.1:47831';
const loopbackHosts = new Set(['localhost', '127.0.0.1', '[::1]']);

export const isHosted = !loopbackHosts.has(window.location.hostname);

export function apiUrl(path) {
  return `${isHosted ? LOOPBACK_ORIGIN : ''}${path}`;
}

export function apiFetch(path, options = {}) {
  const requestOptions = isHosted
    ? {...options, mode: 'cors', targetAddressSpace: 'loopback'}
    : options;
  return fetch(apiUrl(path), requestOptions);
}
