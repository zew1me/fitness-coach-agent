export type LocalStorageMock = {
  clear(): void;
  getItem(key: string): string | null;
  removeItem(key: string): void;
  setItem(key: string, value: string): void;
};

export function createLocalStorageMock(): LocalStorageMock {
  let store: Record<string, string> = {};
  return {
    clear(): void {
      store = {};
    },
    getItem(key: string): string | null {
      return store[key] ?? null;
    },
    removeItem(key: string): void {
      delete store[key];
    },
    setItem(key: string, value: string): void {
      store[key] = value;
    },
  };
}
