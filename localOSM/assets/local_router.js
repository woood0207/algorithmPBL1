/* Offline shortest-distance routing. No browser APIs or network access needed. */
(function (root) {
  'use strict';
  const EARTH = 6371009;
  function distance(a, b) {
    const rad = Math.PI / 180, x = a[0] * rad, y = b[0] * rad;
    const h = Math.sin((y - x) / 2) ** 2 + Math.cos(x) * Math.cos(y) * Math.sin((b[1] - a[1]) * rad / 2) ** 2;
    return 2 * EARTH * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  class Heap {
    constructor() { this.items = []; }
    push(cost, node) {
      const a = this.items, item = [cost, node]; let i = a.length; a.push(item);
      while (i > 0) { const parent = (i - 1) >> 1; if (a[parent][0] <= cost) break; a[i] = a[parent]; i = parent; }
      a[i] = item;
    }
    pop() {
      const a = this.items, best = a[0], last = a.pop();
      if (a.length) { let i = 0; while (true) {
        let child = i * 2 + 1; if (child >= a.length) break;
        if (child + 1 < a.length && a[child + 1][0] < a[child][0]) child++;
        if (last[0] <= a[child][0]) break; a[i] = a[child]; i = child;
      } a[i] = last; } return best;
    }
  }
  function createGraph(data) {
    const n = data.nodes.length, offsets = new Uint32Array(n + 1), incident = new Uint8Array(n);
    for (const [u, v, length, mask] of data.edges) {
      if (!Number.isInteger(u) || !Number.isInteger(v) || u < 0 || v < 0 || u >= n || v >= n || !Number.isFinite(length) || length < 0 || !Number.isInteger(mask) || mask < 1 || mask > 7) throw new Error('Invalid routing edge');
      offsets[u + 1]++; incident[u] |= mask; incident[v] |= mask;
    }
    for (let i = 1; i <= n; i++) offsets[i] += offsets[i - 1];
    const targets = new Uint32Array(data.edges.length), lengths = new Float64Array(data.edges.length), masks = new Uint8Array(data.edges.length), next = offsets.slice();
    for (const [u, v, length, mask] of data.edges) { const i = next[u]++; targets[i] = v; lengths[i] = length; masks[i] = mask; }
    return { nodes: data.nodes, offsets, targets, lengths, masks, incident };
  }
  function nearest(graph, point, mode, maxDistance = 500) {
    let best = null;
    for (let i = 0; i < graph.nodes.length; i++) {
      if (!(graph.incident[i] & mode)) continue;
      const meters = distance(point, graph.nodes[i]);
      if (meters <= maxDistance && (best === null || meters < best.distance_m)) best = { node: i, coordinate: graph.nodes[i], distance_m: meters };
    }
    return best;
  }
  function shortestPath(graph, start, end, mode) {
    const n = graph.nodes.length;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < 0 || start >= n || end >= n) throw new Error('Unknown endpoint');
    const costs = new Float64Array(n).fill(Infinity), previous = new Int32Array(n).fill(-1), previousEdge = new Int32Array(n).fill(-1), heap = new Heap();
    costs[start] = 0; heap.push(0, start);
    while (heap.items.length) {
      const [cost, node] = heap.pop(); if (cost !== costs[node]) continue;
      if (node === end) { const path = [], edges = []; for (let v = end; v !== -1; v = previous[v]) { path.push(v); if (previousEdge[v] !== -1) edges.push(previousEdge[v]); } path.reverse(); edges.reverse(); return { reachable: true, distance_m: cost, path, coordinates: path.map(v => graph.nodes[v]), ...describeModes(graph, path, edges, mode) }; }
      for (let i = graph.offsets[node]; i < graph.offsets[node + 1]; i++) {
        if (!(graph.masks[i] & mode)) continue;
        const target = graph.targets[i], candidate = cost + graph.lengths[i];
        if (candidate < costs[target]) { costs[target] = candidate; previous[target] = node; previousEdge[target] = i; heap.push(candidate, target); }
      }
    }
    return { reachable: false, distance_m: null, path: [], coordinates: [], segments: [], legs: [], distance_by_mode_m: { walk: 0, bike: 0, drive: 0 }, mode_changes: 0 };
  }
  function describeModes(graph, path, edges, allowed) {
    const bits = [1, 2, 4], names = ['walk', 'bike', 'drive'], parent = new Int8Array(edges.length * 3).fill(-1);
    let costs = [0, 0, 0];
    // Minimize changes on the chosen shortest-distance path; transfer count is
    // not a tie-breaker between different equal-distance paths.
    for (let i = 0; i < edges.length; i++) {
      const next = [Infinity, Infinity, Infinity], mask = graph.masks[edges[i]] & allowed;
      for (let mode = 0; mode < 3; mode++) {
        if (!(mask & bits[mode])) continue;
        if (!i) { next[mode] = 0; continue; }
        for (let before = 0; before < 3; before++) {
          const value = costs[before] + (before === mode ? 0 : 1);
          if (value < next[mode]) { next[mode] = value; parent[i * 3 + mode] = before; }
        }
      }
      costs = next;
    }
    const chosen = new Int8Array(edges.length); let last = costs.indexOf(Math.min(...costs));
    for (let i = edges.length - 1; i >= 0; i--) { chosen[i] = last; last = parent[i * 3 + last]; }
    const segments = [], legs = [], totals = { walk: 0, bike: 0, drive: 0 };
    for (let i = 0; i < edges.length; i++) {
      const edge = edges[i], name = names[chosen[i]], length = graph.lengths[edge];
      segments.push({ source: path[i], target: path[i + 1], mode: name, mode_bit: bits[chosen[i]], distance_m: length });
      totals[name] += length;
      let leg = legs[legs.length - 1];
      if (!leg || leg.mode !== name) { leg = { mode: name, distance_m: 0, coordinates: [graph.nodes[path[i]]] }; legs.push(leg); }
      leg.distance_m += length; leg.coordinates.push(graph.nodes[path[i + 1]]);
    }
    return { segments, legs, distance_by_mode_m: totals, mode_changes: Math.max(0, legs.length - 1) };
  }
  root.LocalRouter = { createGraph, nearest, shortestPath, distance };
})(typeof window !== 'undefined' ? window : globalThis);
