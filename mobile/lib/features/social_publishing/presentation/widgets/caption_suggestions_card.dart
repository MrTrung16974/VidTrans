import 'package:flutter/material.dart';

import '../../domain/social_models.dart';

class CaptionSuggestionsCard extends StatelessWidget {
  const CaptionSuggestionsCard({
    super.key,
    required this.suggestions,
    required this.selectedIndex,
    required this.onSelect,
    required this.onApply,
  });

  final CaptionSuggestions suggestions;
  final int selectedIndex;
  final ValueChanged<int> onSelect;
  final VoidCallback onApply;

  @override
  Widget build(BuildContext context) {
    final variant = suggestions.variants[selectedIndex];
    final color = Theme.of(context).colorScheme;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: color.secondaryContainer.withValues(alpha: .38),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: color.secondary.withValues(alpha: .35)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              suggestions.fallback
                  ? 'Gợi ý local dự phòng'
                  : 'Gợi ý bởi ${suggestions.generator}',
              style: Theme.of(context).textTheme.labelMedium,
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              children: [
                for (
                  var index = 0;
                  index < suggestions.variants.length;
                  index++
                )
                  ChoiceChip(
                    label: Text(
                      '${index + 1}. ${suggestions.variants[index].tone}',
                    ),
                    selected: selectedIndex == index,
                    onSelected: (_) => onSelect(index),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              variant.caption,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            if (variant.hashtags.isNotEmpty) ...[
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [
                  for (final tag in variant.hashtags) Chip(label: Text(tag)),
                ],
              ),
            ],
            if (variant.reason.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                variant.reason,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 8),
            FilledButton.tonalIcon(
              onPressed: onApply,
              icon: const Icon(Icons.check_rounded, size: 18),
              label: const Text('Dùng phương án này'),
            ),
          ],
        ),
      ),
    );
  }
}
