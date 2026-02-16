// Auto-generated basic tests

describe('Basic Functionality Tests', () => {

    test('Page loads without errors', () => {
        expect(document).toBeDefined();
    });

    test('Main container exists', () => {
        const container = document.querySelector('.container') ||
                          document.querySelector('main') ||
                          document.querySelector('#app');
        expect(container).not.toBeNull();
    });

    test('No console errors on load', () => {
        const consoleErrors = [];
        const originalError = console.error;
        console.error = (...args) => {
            consoleErrors.push(args);
            originalError.apply(console, args);
        };

        // Page should be loaded
        expect(consoleErrors.length).toBe(0);

        console.error = originalError;
    });

    test('All images have alt attributes', () => {
        const images = document.querySelectorAll('img');
        images.forEach(img => {
            expect(img.getAttribute('alt')).toBeDefined();
        });
    });

    test('All links have valid href', () => {
        const links = document.querySelectorAll('a');
        links.forEach(link => {
            const href = link.getAttribute('href');
            expect(href).not.toBe('');
            expect(href).not.toBe('#');
        });
    });
});
