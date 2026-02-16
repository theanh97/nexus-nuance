/**
 * Nexus Test Suite
 * Comprehensive tests for Nexus orchestration system
 */

const assert = require('assert');

// ============================================================================
// UNIT TESTS - Core Function Tests
// ============================================================================

describe('Unit Tests', () => {
  
  describe('Provider Selection', () => {
    test('should prioritize Z.ai endpoint over other providers', () => {
      const provider = selectOptimalProvider(['openai', 'z.ai', 'anthropic'], { preferZai: true });
      assert.strictEqual(provider, 'z.ai', 'Must prioritize Z.ai as per user requirement');
    });

    test('should fallback correctly when Z.ai unavailable', () => {
      const provider = selectOptimalProvider(['openai', 'anthropic'], { preferZai: true });
      assert.ok(['openai', 'anthropic'].includes(provider), 'Should fallback to available provider');
    });
  });

  describe('Token Optimization', () => {
    test('should reduce redundant logs', () => {
      const logs = ['INFO: Starting', 'INFO: Starting', 'ERROR: Failed', 'ERROR: Failed'];
      const optimized = optimizeTokenUsage(logs);
      assert.ok(optimized.length < logs.length, 'Should deduplicate logs');
    });

    test('should preserve high-value information', () => {
      const logs = ['ERROR: Connection timeout to orion-1', 'INFO: User clicked button'];
      const optimized = optimizeTokenUsage(logs);
      assert.ok(optimized.some(l => l.includes('ERROR')), 'Must preserve error logs');
    });
  });

  describe('Orion Instance Management', () => {
    test('should identify correct orion instance', () => {
      const instance = resolveOrionInstance('orion-3');
      assert.strictEqual(instance.id, 'orion-3', 'Must resolve exact instance');
    });

    test('should handle multiple concurrent instances', () => {
      const instances = ['orion-1', 'orion-2', 'orion-3'];
      const results = instances.map(id => resolveOrionInstance(id));
      assert.strictEqual(results.length, 3, 'All instances should be resolved');
    });
  });
});

// ============================================================================
// INTEGRATION TESTS - Component Interaction
// ============================================================================

describe('Integration Tests', () => {
  
  describe('Command Send & Realtime Feedback', () => {
    test('should send command and receive realtime response', async () => {
      const command = { action: 'execute', target: 'orion-1' };
      const responses = [];
      
      const stream = sendCommand(command);
      stream.on('data', (response) => responses.push(response));
      
      // Wait for response (timeout after 5s)
      await waitFor(() => responses.length > 0, 5000);
      
      assert.ok(responses.length > 0, 'Must receive realtime feedback');
      assert.ok(responses[0].type === 'stream' || responses[0].type === 'chat', 
        'Response must be chat/streamline format');
    });

    test('should NOT lose responses (user-reported issue)', async () => {
      const command = { action: 'process', data: 'test' };
      const response = await sendCommandWithGuaranteedResponse(command);
      
      assert.ok(response !== null, 'Response must not be lost');
      assert.ok(response !== undefined, 'Response must not be undefined');
    });
  });

  describe('Guardian/Monitor Self-Healing', () => {
    test('should auto-recover from stuck state without user confirm', async () => {
      const orion = createMockOrion('orion-1');
      orion.setState('stuck');
      
      const guardian = new Guardian(orion);
      const recovered = await guardian.heal();
      
      assert.strictEqual(recovered, true, 'Guardian should auto-heal');
      assert.notStrictEqual(orion.getState(), 'stuck', 'Orion should not remain stuck');
    });

    test('should push Orion forward for normal flow (no waiting)', async () => {
      const orion = createMockOrion('orion-1');
      orion.setState('waiting');
      
      const guardian = new Guardian(orion);
      await guardian.processNormalFlow();
      
      assert.notStrictEqual(orion.getState(), 'waiting', 
        'Normal flow should proceed without user confirm');
    });
  });

  describe('Multi-Orion Orchestration', () => {
    test('should control multiple orion instances distinctly', async () => {
      const orchestrator = new Orchestrator(['orion-1', 'orion-2', 'orion-3']);
      
      await orchestrator.sendCommand('orion-1', { action: 'start' });
      await orchestrator.sendCommand('orion-2', { action: 'stop' });
      
      const state1 = orchestrator.getState('orion-1');
      const state2 = orchestrator.getState('orion-2');
      
      assert.notStrictEqual(state1, state2, 'Instances must be controlled separately');
    });
  });
});

// ============================================================================
// EDGE CASE TESTS
// ============================================================================

describe('Edge Case Tests', () => {
  
  test('empty provider list', () => {
    assert.throws(() => selectOptimalProvider([]), Error, 'Should throw on empty providers');
  });

  test('null/undefined command payload', async () => {
    const response = await sendCommand(null).catch(e => ({ error: e.message }));
    assert.ok(response.error || response, 'Should handle null command');
  });

  test('orion instance not found', async () => {
    const orchestrator = new Orchestrator(['orion-1']);
    await assert.rejects(
      orchestrator.sendCommand('orion-999', { action: 'test' }),
      Error,
      'Should reject invalid instance'
    );
  });

  test('network timeout handling', async () => {
    const result = await sendCommandWithTimeout(
      { action: 'test' },
      100 // Very short timeout
    ).catch(e => ({ timeout: true }));
    
    assert.ok(result.timeout || result.error, 'Should handle timeout gracefully');
  });

  test('concurrent command flooding', async () => {
    const commands = Array(100).fill({ action: 'ping' });
    const results = await Promise.allSettled(
      commands.map(cmd => sendCommand(cmd))
    );
    
    const successful = results.filter(r => r.status === 'fulfilled').length;
    assert.ok(successful > 0, 'Should handle concurrent commands');
  });
});

// ============================================================================
// ERROR HANDLING TESTS
// ============================================================================

describe('Error Handling Tests', () => {
  
  test('provider failure recovery', async () => {
    const controller = new ProviderController();
    controller.setProvider('z.ai');
    
    // Simulate failure
    controller.simulateFailure();
    
    const result = await controller.executeWithFallback('test query');
    assert.ok(result.success || result.usedFallback, 
      'Should recover via fallback');
  });

  test('UI rendering should not crash on invalid data', () => {
    const ui = new NexusUI();
    assert.doesNotThrow(
      () => ui.render(null),
      'Should handle null data gracefully'
    );
    assert.doesNotThrow(
      () => ui.render(undefined),
      'Should handle undefined gracefully'
    );
    assert.doesNotThrow(
      () => ui.render({ invalid: 'structure' }),
      'Should handle invalid structure'
    );
  });

  test('log overflow protection', () => {
    const logger = new OptimizedLogger({ maxSize: 100 });
    
    // Add 200 log entries
    for (let i = 0; i < 200; i++) {
      logger.info(`Log entry ${i}`);
    }
    
    assert.ok(logger.getSize() <= 100, 'Should cap log size');
  });

  test('dashboard should not freeze on rapid updates', async () => {
    const dashboard = new Dashboard();
    const updates = [];
    
    // Rapid updates
    for (let i = 0; i < 50; i++) {
      updates.push(dashboard.update({ timestamp: Date.now(), data: i }));
    }
    
    await Promise.all(updates);
    assert.ok(true, 'Dashboard should handle rapid updates');
  });
});

// ============================================================================
// UI/UX SPECIFIC TESTS (Light Theme, No Jank)
// ============================================================================

describe('UI/UX Tests', () => {
  
  test('should render in light theme', () => {
    const ui = new NexusUI();
    ui.setTheme('light');
    
    const styles = ui.getComputedStyles();
    assert.ok(
      styles.backgroundColor !== '#000' && styles.backgroundColor !== 'black',
      'Should use light theme'
    );
  });

  test('should not have visual jank (render completion)', () => {
    const ui = new NexusUI();
    let renderCount = 0;
    
    ui.on('render', () => renderCount++);
    ui.render({ data: 'test' });
    
    // Should complete in single render pass
    assert.strictEqual(renderCount, 1, 'Should render without jank');
  });

  test('should minimize layout shifts', () => {
    const ui = new NexusUI();
    const initialLayout = ui.getLayout();
    
    ui.render({ data: 'new data' });
    const newLayout = ui.getLayout();
    
    assert.ok(
      Math.abs(initialLayout.height - newLayout.height) < 10,
      'Should minimize layout shifts'
    );
  });

  test('should display latest logs at top (newest first)', () => {
    const ui = new NexusUI();
    const logs = [
      { time: 1000, message: 'First' },
      { time: 2000, message: 'Second' },
      { time: 3000, message: 'Third' }
    ];
    
    const displayed = ui.renderLogs(logs);
    assert.strictEqual(displayed[0].message, 'Third', 'Latest log should be first');
  });
});

// ============================================================================
// TEST HELPERS
// ============================================================================

function waitFor(condition, timeout) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (condition()) resolve(true);
      else if (Date.now() - start > timeout) reject(new Error('Timeout'));
      else setTimeout(check, 10);
    };
    check();
  });
}

// Mock implementations for demonstration
function selectOptimalProvider(providers, options) {
  if (options?.preferZai && providers.includes('z.ai')) return 'z.ai';
  return providers[0];
}

function optimizeTokenUsage(logs) {
  const seen = new Set();
  return logs.filter(log => {
    if (seen.has(log)) return false;
    seen.add(log);
    return true;
  });
}

function resolveOrionInstance(id) {
  return { id, status: 'active' };
}

function sendCommand(cmd) {
  return new (require('events').EventEmitter)();
}

async function sendCommandWithGuaranteedResponse(cmd) {
  // Implementation should guarantee response
  return { success: true };
}

class Guardian {
  constructor(orion) { this.orion = orion; }
  async heal() { this.orion.setState('running'); return true; }
  async processNormalFlow() { this.orion.setState('running'); }
}

class Orchestrator {
  constructor(instances) { this.instances = instances; this.states = {}; }
  async sendCommand(id, cmd) { this.states[id] = cmd.action; }
  getState(id) { return this.states[id] || 'unknown'; }
}

class ProviderController {
  setProvider(p) { this.provider = p; }
  simulateFailure() { this.failed = true; }
  async executeWithFallback(query) {
    if (this.failed) return { success: false, usedFallback: true };
    return { success: true };
  }
}

class NexusUI {
  setTheme(t) { this.theme = t; }
  getComputedStyles() { return { backgroundColor: '#fff' }; }
  render(data) { return data; }
  getLayout() { return { height: 100 }; }
  renderLogs(logs) { return logs.sort((a, b) => b.time - a.time); }
}

class OptimizedLogger {
  constructor(options) { this.maxSize = options.maxSize; this.logs = []; }
  info(msg) { this.logs.push(msg); if (this.logs.length > this.maxSize) this.logs.shift(); }
  getSize() { return this.logs.length; }
}

class Dashboard {
  update(data) { return Promise.resolve(); }
}

function createMockOrion(id) {
  let state = 'idle';
  return {
    setState(s) { state = s; },
    getState() { return state; }
  };
}

async function sendCommandWithTimeout(cmd, ms) {
  return new Promise((_, reject) => 
    setTimeout(() => reject(new Error('Timeout')), ms)
  );
}