import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
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
    return StudioCard(
      backgroundColor: AppColors.brandTint,
      borderColor: AppColors.brandBorder,
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.auto_awesome_rounded,
                size: 16,
                color: AppColors.brandDark,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  suggestions.fallback
                      ? 'Gợi ý local dự phòng'
                      : 'Gợi ý bởi ${suggestions.generator}',
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w800,
                    color: AppColors.brandDark,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 6,
            runSpacing: 6,
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
                  selectedColor: AppColors.brand,
                  backgroundColor: Colors.white,
                  side: BorderSide(
                    color: selectedIndex == index
                        ? AppColors.brand
                        : AppColors.brandBorder,
                  ),
                  labelStyle: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: selectedIndex == index
                        ? Colors.white
                        : AppColors.ink,
                  ),
                  onSelected: (_) => onSelect(index),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppColors.line),
            ),
            child: Text(
              variant.caption,
              style: const TextStyle(
                fontSize: 13.5,
                color: AppColors.ink,
                height: 1.4,
              ),
            ),
          ),
          if (variant.hashtags.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                for (final tag in variant.hashtags)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.tealSoft,
                      borderRadius: BorderRadius.circular(999),
                      border: Border.all(color: AppColors.tealBorder),
                    ),
                    child: Text(
                      tag,
                      style: const TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                        color: AppColors.tealDark,
                      ),
                    ),
                  ),
              ],
            ),
          ],
          if (variant.reason.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              variant.reason,
              style: const TextStyle(
                fontSize: 11.5,
                color: AppColors.muted,
                fontStyle: FontStyle.italic,
              ),
            ),
          ],
          const SizedBox(height: 10),
          FilledButton.tonalIcon(
            style: FilledButton.styleFrom(
              backgroundColor: AppColors.brand,
              foregroundColor: Colors.white,
            ),
            onPressed: onApply,
            icon: const Icon(Icons.check_rounded, size: 18),
            label: const Text(
              'Dùng phương án này',
              style: TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }
}
