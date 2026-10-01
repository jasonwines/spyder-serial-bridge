# shellcheck shell=sh
# Installed to /etc/profile.d by install.sh; sourced by login shells.
#
# Offers to change the login password while it's still the SD image's
# default. prepare-image.sh leaves the marker file in the login user's home;
# changing the password here removes it. Answering N asks again next login.
if [ -f "$HOME/.spyder-default-password" ] && [ -t 0 ]; then
    printf '\nThis login still uses the default password (spyder), which anyone can look up.\n'
    printf 'Change it now? [y/N] '
    read -r spyder_answer
    case $spyder_answer in
        [yY]*)
            if passwd; then
                rm -f "$HOME/.spyder-default-password"
                echo "Login password changed. (The config page has its own password.)"
            else
                echo "Not changed; you'll be asked again next time you log in."
            fi
            ;;
        *) echo "You'll be asked again next time you log in." ;;
    esac
    unset spyder_answer
fi
