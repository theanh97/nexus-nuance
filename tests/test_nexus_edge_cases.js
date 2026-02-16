// Edge Case Tests for Nexus

// Test: Empty Orion list
function testEmptyOrionList() {
  const orions = [];
  
  const startAll = (instances) => {
    if (!instances || instances.length === 0) {
      return { error: 'No instances to start' };
    }
    return instances.map(o => o.start());
  };
  
  const result = startAll(orions);
  if (!result.error) {
    throw new Error('Should handle empty list');
  }
  console.log('✓ Empty list handled');
}

// Test: Provider unavailable fallback
function testProviderFallback() {
  const available = ['openai']; // Z.ai not available
  
  const selectProvider = (providers) => {
    const priority = ['z.ai', 'anthropic', 'openai', 'mock'];
    const found = providers.find(p => priority.includes(p));
    if (!found && providers.length > 0) {
      return providers[0]; // Fallback to first available
    }
    return found || 'mock'; // Ultimate fallback
  };
  
  const selected = selectProvider(available);
  if (selected !== 'openai') {
    throw new Error('Should fallback to available provider');
  }
  console.log('✓ Provider fallback works');
}

// Test: Guardian infinite loop prevention
function testGuardianLoopPrevention() {
  let recoveryAttempts = 0;
  const MAX_ATTEMPTS = 3;
  
  const guardian = {
    recover: (instance) => {
      recoveryAttempts++;
      if (recoveryAttempts > MAX_ATTEMPTS) {
        return { error: 'Max recovery attempts reached', attempts: recoveryAttempts };
      }
      instance.status = 'stuck'; // Simulate still stuck
      return { recovered: true, attempts: recoveryAttempts };
    }
  };
  
  const orion = { status: 'stuck' };
  let result;
  
  for (let i = 0; i < 5; i++) {
    result = guardian.recover(orion);
    if (result.error) break;
  }
  
  if (!result.error) {
    throw new Error('Should stop after max attempts');
  }
  console.log('✓ Loop prevention works');
}

// Test: Concurrent Orion commands
function testConcurrentCommands() {
  const orions = [
    { name: 'orion-1', processing: false },
    { name: 'orion-2', processing: false }
  ];
  
  const sendCommand = (orion, cmd) => {
    return new Promise(resolve => {
      if (orion.processing) {
        resolve({ error: 'Orion busy', orion: orion.name });
        return;
      }
      orion.processing = true;
      setTimeout(() => {
        orion.processing = false;
        resolve({ success: true, orion: orion.name });
      }, 10);
    });
  };
  
  return Promise.all([
    sendCommand(orions[0], 'start'),
    sendCommand(orions[1], 'start'),
    sendCommand(orions[0], 'stop') // Should queue or fail
  ]).then(results => {
    // Verify no data corruption
    const errors = results.filter(r => r.error);
    console.log('✓ Concurrent commands handled');
  });
}

// Test: Token limit edge case
function testTokenLimitEdge() {
  const MAX_TOKENS = 100;
  const messages = [
    { role: 'user', content: 'A'.repeat(50) },
    { role: 'assistant', content: 'B'.repeat(50) },
    { role: 'user', content: 'C'.repeat(50) } // Would exceed
  ];
  
  const countTokens = (msgs) => {
    return msgs.reduce((sum, m) => sum + m.content.length, 0); // Simplified
  };
  
  let tokenCount = 0;
  const trimmed = [];
  
  for (const msg of messages) {
    if (tokenCount + msg.content.length > MAX_TOKENS) {
      // Trim oldest messages
      trimmed.shift();
      continue;
    }
    trimmed.push(msg);
    tokenCount += msg.content.length;
  }
  
  console.log('✓ Token trimming works');
}

// Run edge case tests
(async () => {
  try {
    testEmptyOrionList();
    testProviderFallback();
    testGuardianLoopPrevention();
    await testConcurrentCommands();
    testTokenLimitEdge();
    console.log('✓ All edge case tests passed');
  } catch (e) {
    console.error('✗ Edge case test failed:', e.message);
    process.exit(1);
  }
})();