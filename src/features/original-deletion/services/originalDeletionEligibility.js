export function isOriginalDeletionEligible({
  asset,
  capabilities,
  mappingState,
  outcome,
  status,
}) {
  if (
    asset?.preview_status !== 'preview_ready'
    || asset?.review_status !== 'preview_confirmed'
    || mappingState?.status !== 'available'
    || outcome?.status === 'deleted'
    || status === 'loading'
    || status === 'deleting'
  ) {
    return false;
  }

  if (
    ['image', 'video'].includes(asset?.type)
    && asset?.verification_status === 'server_hash_recorded'
  ) {
    return true;
  }

  return Boolean(
    asset?.type === 'video'
    && asset?.verification_status === 'file_verified'
    && capabilities?.features?.formalAppleLogPreview === true
    && asset?.formal_preview?.state === 'ready'
  );
}
