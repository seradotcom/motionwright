/** Closed diagnostics from an original Graph context rejection; never admission. */
const stages = ['ADMISSION', 'BINDING', 'CUSTODY', 'LOCAL', 'TRANSPORT', 'NATIVE'] as const;
const errorCodes = ['Unsupported', 'Unavailable', 'PermissionDenied', 'ConsentRequired',
  'PolicyDenied', 'NotFound', 'AmbiguousTarget', 'StaleReference', 'BackendFailed',
  'Timeout', 'InvalidArgument', 'PluginProtocolError', 'SandboxDenied', 'Conflict',
  'Cancelled', 'Internal', 'ProtocolMismatch', 'ResourceExhausted'] as const;

export type GraphContextFailureStage = typeof stages[number];
export type GraphContextFailureCode = typeof errorCodes[number];

export interface GraphContextFailure {
  schema: 'motionwright.native.graph-context-failure/1';
  stage: GraphContextFailureStage;
  error_code: GraphContextFailureCode | null;
}

const fields = ['schema', 'stage', 'error_code'] as const;

/** Decode only the closed original rejection object, without parsing diagnostics.
 * A matching shape conveys neither provenance nor permission to author a Graph. */
export function decodeGraphContextFailure(value: unknown): GraphContextFailure | null {
  try {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) return null;
    const keys = Reflect.ownKeys(value);
    if (keys.length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) return null;
    const descriptors = Object.getOwnPropertyDescriptors(value);
    if (fields.some(field => !('value' in descriptors[field]) || !descriptors[field].enumerable)) return null;
    const schema: unknown = descriptors.schema.value;
    const stage: unknown = descriptors.stage.value;
    const code: unknown = descriptors.error_code.value;
    if (schema !== 'motionwright.native.graph-context-failure/1'
        || typeof stage !== 'string' || !stages.includes(stage as GraphContextFailureStage)
        || (code !== null && (typeof code !== 'string' || !errorCodes.includes(code as GraphContextFailureCode)))) return null;
    return { schema, stage: stage as GraphContextFailureStage, error_code: code as GraphContextFailureCode | null };
  } catch {
    // Malformed accessors or proxies cannot supply a trusted diagnostic.
    return null;
  }
}
