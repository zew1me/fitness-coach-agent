export type UploadedFileStubInput = {
  filename: string;
  mediaType: string;
  url: string;
};

export type UploadedFileStub = {
  contentType: string;
  filename: string;
  objectKey: string;
  publicUrl: string | null;
};

const FILENAME_PREFIX = "Uploaded file: ";
const CONTENT_TYPE_PREFIX = "content_type=";
const PUBLIC_URL_PREFIX = "public_url=";
const OBJECT_KEY_PREFIX = "object_key=";
const STUB_PREFIXES = [
  FILENAME_PREFIX,
  CONTENT_TYPE_PREFIX,
  PUBLIC_URL_PREFIX,
  OBJECT_KEY_PREFIX,
] as const;

function objectKeyFromUrl(url: string): string {
  try {
    return new URL(url).pathname.replace(/^\//, "");
  } catch {
    return url;
  }
}

export function uploadedFileText(file: UploadedFileStubInput): string {
  const objectKey = objectKeyFromUrl(file.url);

  return (
    `${FILENAME_PREFIX}${file.filename}\n` +
    `${CONTENT_TYPE_PREFIX}${file.mediaType}\n` +
    `${PUBLIC_URL_PREFIX}${file.url}\n` +
    `${OBJECT_KEY_PREFIX}${objectKey}`
  );
}

function valueAfterPrefix(line: string, prefix: string): string | null {
  return line.startsWith(prefix) ? line.slice(prefix.length) : null;
}

export function parseUploadedFileText(text: string): UploadedFileStub | null {
  const lines = text.trim().split("\n");
  if (lines.length !== STUB_PREFIXES.length) {
    return null;
  }

  const values: string[] = [];
  for (const [index, line] of lines.entries()) {
    const prefix = STUB_PREFIXES[index];
    if (prefix === undefined) {
      return null;
    }

    const value = valueAfterPrefix(line, prefix);
    if (value === null) {
      return null;
    }
    values.push(value);
  }

  const [filename, contentType, publicUrl, objectKey] = values as [
    string,
    string,
    string,
    string,
  ];

  if (
    filename.length === 0 ||
    contentType.length === 0 ||
    objectKey.length === 0
  ) {
    return null;
  }

  return {
    contentType,
    filename,
    objectKey,
    publicUrl: publicUrl.length > 0 ? publicUrl : null,
  };
}
