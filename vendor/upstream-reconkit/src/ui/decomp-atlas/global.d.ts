declare module '*.png' {
  const src: string;
  export default src;
}

interface ReconstructKitConfig {
  serverBaseUrl: string;
  projectRoot: string;
  target: string;
}

interface Window {
  __RECONKIT_CONFIG__?: ReconstructKitConfig;
}
