import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';
import { cn } from '@/lib/utils';

const buttonVariants = cva('ui-button', {
  variants: {
    variant: {
      default: 'ui-button-default',
      destructive: 'ui-button-destructive',
      outline: 'ui-button-outline',
      secondary: 'ui-button-secondary',
      ghost: 'ui-button-ghost',
      link: 'ui-button-link',
    },
    size: {
      default: 'ui-button-size-default',
      sm: 'ui-button-size-sm',
      lg: 'ui-button-size-lg',
      icon: 'ui-button-size-icon',
    },
  },
  defaultVariants: { variant: 'default', size: 'default' },
});

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type = 'button', ...props }, ref) => {
    const Component = asChild ? Slot : 'button';
    return <Component ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
