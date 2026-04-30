-- Supabase RPC for paginated export of document pages + chunk summaries.
-- Apply via: Supabase SQL editor.
--
-- Note: id columns in this schema are stored as TEXT (Prisma String without
-- @db.Uuid), so the function signature uses text/text[], not uuid/uuid[].
--
-- Call repeatedly with increasing p_offset until fewer than p_limit rows
-- come back. Order is stable: (created_at DESC, document_id, page_index).

drop function if exists public.export_asset_pages(uuid[], int, int);
drop function if exists public.export_asset_pages(text[], int, int);

create or replace function public.export_asset_pages(
  p_asset_ids text[],
  p_limit     int default 2000,
  p_offset    int default 0
)
returns table (
  id                      text,
  document_id             text,
  page_index              int,
  original_path           text,
  rotation_deg            int,
  is_blank                boolean,
  is_template_empty       boolean,
  is_removed              boolean,
  extracted_json          jsonb,
  enhanced_s3_key         text,
  created_at              timestamptz,
  file_name               text,
  file_type               text,
  asset_id                text,
  chunk_count             bigint,
  chunks_with_embeddings  bigint,
  chunks                  json
)
language sql
stable
security definer
set search_path = public
as $$
  select
    dp.id,
    dp.document_id,
    dp.page_index,
    dpr.original_path,
    dp.rotation_deg,
    dp.is_blank,
    dp.is_template_empty,
    dp.is_removed,
    dp.extracted_json,
    dp.enhanced_s3_key,
    dp.created_at,
    dpr.file_name,
    dpr.file_type,
    dpr.asset_id,
    count(dc.id)                                                   as chunk_count,
    count(case when dc.embeddings is not null then 1 end)          as chunks_with_embeddings,
    coalesce(
      json_agg(
        json_build_object(
          'chunk_id',      dc.id,
          'chunk_index',   dc.chunk_index,
          'text_content',  left(dc.text_content, 200),
          'token_count',   dc.token_count,
          'has_embedding', dc.embeddings is not null,
          'metadata',      dc.metadata
        ) order by dc.chunk_index
      ) filter (where dc.id is not null),
      '[]'::json
    ) as chunks
  from document_pages dp
  inner join document_processing_records dpr
    on dp.document_id = dpr.id
  left join document_chunks dc
    on dc.page_id = dp.id
  where dpr.asset_id = any (p_asset_ids)
  group by
    dp.id, dp.document_id, dp.page_index, dpr.original_path,
    dp.rotation_deg, dp.is_blank, dp.is_template_empty, dp.is_removed,
    dp.extracted_json, dp.enhanced_s3_key, dp.created_at,
    dpr.file_name, dpr.file_type, dpr.asset_id
  order by dp.created_at desc, dp.document_id, dp.page_index
  limit  p_limit
  offset p_offset;
$$;

grant execute on function public.export_asset_pages(text[], int, int) to service_role;
-- grant execute on function public.export_asset_pages(text[], int, int) to authenticated;
