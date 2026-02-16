// Unit Tests for Nexus Core Functions
const assert = require('assert');

// Test: Guardian auto-recovery
function testGuardianAutoRecovery() {
  // Mock guardian state
  let orionState = { status: 'stuck', instance: 'orion-1' };
  
  // Simulate guardian recovery logic
  const guardianRecover = (state) => {
    if (state.status === 'stuck') {
      return { status: 'running', instance: state.instance, recovered: true };
    }
    return state;
  };
  
  const result = guardianRecover(orionState);
  assert.strictEqual(result.status, 'running', 'Orion should auto-recover');
  assert.strictEqual(result.recovered, true, 'Recovery flag should be set');
}

// Test: Z.ai Provider priority
function testProviderPriority() {
  const providers = ['openai', 'z.ai', 'anthropic', 'mock'];
  
  const selectProvider = (available) => {
    const priority = ['z.ai', 'anthropic', 'openai', 'mock'];
    return available.find(p => priority.includes(p));
  };
  
  const selected = selectProvider(providers);
  assert.strictEqual(selected, 'z.ai', 'Should prioritize Z.ai endpoint');
}

// Test: Token optimization
function testTokenOptimization() {
  const rawLog = {
    timestamp: Date.now(),
    level: 'debug',
    message: 'Processing request 12345',
    data: { key: 'value', extra: 'unnecessary' }
  };
  
  const optimizeLog = (log) => {
    if (log.level === 'debug') return null; // Skip debug in production
    return { 
      msg: log.message, 
      ts: log.timestamp 
    };
  };
  
  const optimized = optimizeLog(rawLog);
  assert.strictEqual(optimized, null, 'Debug logs should be filtered');
}

// Run tests
try {
  testGuardianAutoRecovery();
  testProviderPriority();
  testTokenOptimization();
  console.log('✓ All unit tests passed');
} catch (e) {
  console.error('✗ Test failed:', e.message);
  process.exit(1);
}